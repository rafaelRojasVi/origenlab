"""Tests for the V2 commercial-case command boundary.

Two layers, and the split is the same one `test_v2_command_boundary.py` makes.

**In process, always run.** What a request must carry, what is refused before a connection is
opened, that the case router exposes nothing but POST, and that the stage table in Python is
the stage table in the migration. None of it needs a database, so all of it runs in CI.

**Database-backed, opt-in.** The properties that are only true if PostgreSQL says so: that a
case and its origin evidence are one transaction, that a refused command writes nothing at
all — not even its own receipt — that two operators racing one case do not both win, that a
terminal case is never revived, and that no command anywhere in this file moves a row in
`outbound.*`. These skip without `ORIGENLAB_V2_TEST_DSN`.

Those tests run in a **disposable database the harness creates and drops**, never in a
development database and never in the clean room. See `v2_command_harness`: it is a property
of the harness, not a cleanup convention that could be forgotten.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.case_commands import (
    ADD_CASE_ORGANIZATION,
    ADVANCE_CASE_STAGE,
    CASE_COMMAND_NAMES,
    CASE_ORGANIZATION_ROLES,
    CASE_STAGES,
    EVIDENCE_RELATIONS,
    LINK_CASE_EVIDENCE,
    OPEN_COMMERCIAL_CASE,
    RECORD_CASE_INTEREST,
    SET_CASE_ORGANIZATION_ROLE,
    STAGE_TRANSITIONS,
    AddCaseOrganizationBody,
    AdvanceCaseStageBody,
    LinkCaseEvidenceBody,
    OpenCommercialCaseBody,
    RecordCaseInterestBody,
    SetCaseOrganizationRoleBody,
    stage_transition_allowed,
    validated_case,
)
from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup

LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
CASE_ID = "aaaaaaaa-1111-4111-8111-111111111111"
RECORD_ID = "bbbbbbbb-2222-4222-8222-222222222222"
ORGANIZATION_ID = "cccccccc-3333-4333-8333-333333333333"

#: Every case route, with a body that would be accepted if the caller were allowed to decide.
#: Used by the authorization and header tests, so a route added without one of the four
#: universal requirements fails here rather than in production.
ROUTES: tuple[tuple[str, str, dict], ...] = (
    (
        "/v2/commands/open-commercial-case",
        OPEN_COMMERCIAL_CASE,
        {"title": "Caso", "origin_source_record_id": RECORD_ID, "note": "porque sí"},
    ),
    (
        "/v2/commands/link-case-evidence",
        LINK_CASE_EVIDENCE,
        {
            "opportunity_id": CASE_ID,
            "opportunity_version": 1,
            "relation": "mentions",
            "source_record_id": RECORD_ID,
            "note": "porque sí",
        },
    ),
    (
        "/v2/commands/add-case-organization",
        ADD_CASE_ORGANIZATION,
        {
            "opportunity_id": CASE_ID,
            "opportunity_version": 1,
            "organization_id": ORGANIZATION_ID,
            "organization_version": 1,
            "role": "mentioned",
            "note": "porque sí",
        },
    ),
    (
        "/v2/commands/set-case-organization-role",
        SET_CASE_ORGANIZATION_ROLE,
        {
            "opportunity_id": CASE_ID,
            "opportunity_version": 1,
            "opportunity_organization_id": ORGANIZATION_ID,
            "role": "mentioned",
            "note": "porque sí",
        },
    ),
    (
        "/v2/commands/record-case-interest",
        RECORD_CASE_INTEREST,
        {
            "opportunity_id": CASE_ID,
            "opportunity_version": 1,
            "model_text": "UP200Ht",
            "note": "porque sí",
        },
    ),
    (
        "/v2/commands/advance-case-stage",
        ADVANCE_CASE_STAGE,
        {
            "opportunity_id": CASE_ID,
            "opportunity_version": 1,
            "stage": "qualifying",
            "note": "porque sí",
        },
    ),
)


class _Lookup(OperatorLookup):
    def __init__(self, operator: OperatorIdentity | None) -> None:
        self._operator = operator

    def by_email(self, email_norm: str) -> OperatorIdentity | None:
        return self._operator


def _operator(role: str = "sales", status: str = "active") -> OperatorIdentity:
    return OperatorIdentity(
        operator_id="00000000-0000-4000-8000-000000000001",
        email_norm="operator@example.cl",
        display_name="Operator",
        role=role,
        status=status,
    )


class _FakeCaseRepo:
    """Records what the router asked for, and answers whatever the test needs."""

    def __init__(self, *, raises: CommandRefused | None = None) -> None:
        self.calls: list[dict] = []
        self.raises = raises

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        return {"command": kwargs["command_name"], "created": [], "replayed": False}


def _client(repo: _FakeCaseRepo, *, operator: OperatorIdentity | None = None) -> TestClient:
    from fastapi import FastAPI

    from origenlab_api.v2.case_command_routes import case_command_router

    app = FastAPI()
    app.include_router(case_command_router)
    app.state.v2_case_command_repository = repo
    app.state.v2_identity = LocalDevIdentity(
        LOOPBACK, _Lookup(operator if operator is not None else _operator())
    )
    return TestClient(app)


def _headers(key: str | None = "key-1") -> dict[str, str]:
    headers = {OPERATOR_EMAIL_HEADER: "operator@example.cl"}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


# ------------------------------------------------------------------ the shape of the surface


def test_every_case_command_has_a_route_and_every_route_a_command() -> None:
    """The vocabulary and the surface are the same list, counted from both ends."""
    assert {command for _, command, _ in ROUTES} == set(CASE_COMMAND_NAMES)
    assert len(ROUTES) == len(CASE_COMMAND_NAMES) == 6


def test_the_case_router_exposes_nothing_but_post() -> None:
    """A command boundary that answered GET would be a read surface nobody audited."""
    from origenlab_api.v2.case_command_routes import case_command_router

    for route in case_command_router.routes:
        assert set(route.methods) == {"POST"}, route.path
        assert route.path.startswith("/v2/commands/")


@pytest.mark.parametrize(("path", "_command", "body"), ROUTES)
def test_no_identity_is_401(path: str, _command: str, body: dict) -> None:
    response = _client(_FakeCaseRepo(), operator=None).post(
        path, json=body, headers={"Idempotency-Key": "key-1"}
    )
    assert response.status_code == 401


@pytest.mark.parametrize(("path", "_command", "body"), ROUTES)
def test_a_viewer_may_not_decide_a_case(path: str, _command: str, body: dict) -> None:
    """403, not 401. We know exactly who this is, and the answer is still no."""
    repo = _FakeCaseRepo()
    response = _client(repo, operator=_operator(role="viewer")).post(
        path, json=body, headers=_headers()
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "role_may_not_decide"
    assert repo.calls == []


@pytest.mark.parametrize(("path", "_command", "body"), ROUTES)
def test_every_case_command_requires_an_idempotency_key(
    path: str, _command: str, body: dict
) -> None:
    repo = _FakeCaseRepo()
    response = _client(repo).post(path, json=body, headers=_headers(key=None))
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "idempotency_key_required"
    assert repo.calls == []


@pytest.mark.parametrize(("path", "_command", "body"), ROUTES)
def test_every_case_command_requires_a_reason(path: str, _command: str, body: dict) -> None:
    """A durable decision whose reason is "   " is a decision nobody can audit later."""
    repo = _FakeCaseRepo()
    response = _client(repo).post(path, json={**body, "note": "   "}, headers=_headers())
    assert response.status_code == 422
    assert repo.calls == []


@pytest.mark.parametrize(("path", "_command", "body"), ROUTES)
def test_the_operator_is_never_taken_from_the_body(path: str, _command: str, body: dict) -> None:
    """A caller cannot decide *as* somebody else, and cannot even ask to."""
    repo = _FakeCaseRepo()
    response = _client(repo).post(
        path,
        json={**body, "operator_id": "00000000-0000-4000-8000-0000000000ff"},
        headers=_headers(),
    )
    assert response.status_code == 422
    assert repo.calls == []


@pytest.mark.parametrize(("path", "_command", "body"), ROUTES)
def test_an_unknown_field_is_refused_rather_than_dropped(
    path: str, _command: str, body: dict
) -> None:
    """`extra="forbid"` is what keeps a price, a consent or a campaign out of every request.

    A field that is silently discarded is worse than one that is refused: the operator
    believes it was recorded, and nothing in the audit trail contradicts them.
    """
    repo = _FakeCaseRepo()
    for smuggled in ("unit_price", "currency", "campaign_id", "opt_in", "audience"):
        response = _client(repo).post(path, json={**body, smuggled: "1"}, headers=_headers())
        assert response.status_code == 422, smuggled
    assert repo.calls == []


@pytest.mark.parametrize(("path", "command", "body"), ROUTES)
def test_the_router_hands_the_repository_the_command_it_named(
    path: str, command: str, body: dict
) -> None:
    repo = _FakeCaseRepo()
    response = _client(repo).post(path, json=body, headers=_headers())
    assert response.status_code == 200, response.text
    assert len(repo.calls) == 1
    call = repo.calls[0]
    assert call["command_name"] == command
    assert call["idempotency_key"] == "key-1"
    assert call["operator"].operator_id == _operator().operator_id
    assert call["fields"]["note"] == "porque sí"


# ------------------------------------------------------------------ no money, ever


def test_no_case_request_can_carry_an_amount() -> None:
    """An interest is never a commitment and never a price (DOMAIN.md §3.6.2).

    Asserted over the model's own fields rather than by trying values, so a field added later
    is caught by name even if nobody writes a test for it.
    """
    money = {"price", "amount", "currency", "total", "margin", "cost", "discount", "value"}
    for body_type in (
        OpenCommercialCaseBody,
        LinkCaseEvidenceBody,
        AddCaseOrganizationBody,
        SetCaseOrganizationRoleBody,
        RecordCaseInterestBody,
        AdvanceCaseStageBody,
    ):
        for name in body_type.model_fields:
            assert not (set(name.split("_")) & money), f"{body_type.__name__}.{name}"


def test_no_case_request_can_carry_a_marketing_field() -> None:
    """Nothing in the case model touches `outbound.*`, in either direction (§3.6.4)."""
    marketing = {
        "campaign", "consent", "optin", "opt", "subscription", "audience", "recipient",
        "send", "unsubscribe", "permission", "mailing",
    }
    for body_type in (
        OpenCommercialCaseBody,
        LinkCaseEvidenceBody,
        AddCaseOrganizationBody,
        SetCaseOrganizationRoleBody,
        RecordCaseInterestBody,
        AdvanceCaseStageBody,
    ):
        for name in body_type.model_fields:
            assert not (set(name.split("_")) & marketing), f"{body_type.__name__}.{name}"


def test_the_repository_never_names_outbound_or_a_person() -> None:
    """Read from the source, because a table this boundary cannot name it cannot write."""
    import pathlib

    import origenlab_api.v2.case_command_repository as module

    source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
    body = source.partition('"""')[2].partition('"""')[2]  # skip the module docstring
    for forbidden in (
        "outbound.",
        "crm.person",
        "crm.affiliation",
        "crm.quote",
        "crm.task",
        "crm.activity",
        "crm.organization_domain",
    ):
        assert forbidden not in body, forbidden
    # The one relationship table it may *read*, and only to decide whether the operator owes
    # a justification. It never writes one.
    assert "crm.organization_relationship" in body
    assert "insert into crm.organization_relationship" not in body


def test_no_case_command_can_write_a_machine_proposal() -> None:
    """Every row these commands write is an operator's, with the operator named.

    Read as SQL rather than as a substring count. `machine_proposed` is legitimately readable
    — a proposal is confirmable, and the event that confirms one says what it was — but in
    the statements themselves it may appear **only** as a predicate. One occurrence in an
    INSERT's values or an UPDATE's SET would mean this boundary can author a proposal, and
    the only thing that stops a machine from holding a commercial opinion is that nothing can
    write one.
    """
    import pathlib
    import re

    import origenlab_api.v2.case_command_repository as module

    source = pathlib.Path(module.__file__).read_text(encoding="utf-8")
    statements = re.findall(r'"""(\s*(?:insert|update|select|delete)[\s\S]*?)"""', source,
                            re.IGNORECASE)
    assert statements, "the repository should contain SQL"
    read_as_a_predicate = 0
    for statement in statements:
        if "machine_proposed" not in statement:
            continue
        before, _, _after = statement.lower().partition("machine_proposed")
        assert "where" in before, statement
        assert not statement.lower().lstrip().startswith("insert"), statement
        read_as_a_predicate += 1
    assert read_as_a_predicate == 1, "exactly one statement reads a proposal, to confirm it"

    # And every confirmation these statements write is `confirmed`, never anything else.
    for statement in statements:
        if "confirmation" in statement and statement.lower().lstrip().startswith("insert"):
            assert "'confirmed'" in statement, statement


# ------------------------------------------------------------------ the request rules


def test_a_case_is_never_opened_without_the_document_that_caused_it() -> None:
    with pytest.raises(Exception):
        OpenCommercialCaseBody(title="Caso", note="x")


def test_a_case_is_never_opened_at_a_stage_of_the_operators_choosing() -> None:
    """There is no `stage` field. A case is born at `lead`, and a trigger says so too."""
    assert "stage" not in OpenCommercialCaseBody.model_fields


def test_an_evidence_link_names_exactly_one_subject() -> None:
    base = {"opportunity_id": CASE_ID, "opportunity_version": 1, "relation": "mentions",
            "note": "x"}
    with pytest.raises(Exception):
        LinkCaseEvidenceBody(**base)
    with pytest.raises(Exception):
        LinkCaseEvidenceBody(**base, source_record_id=RECORD_ID, message_id=RECORD_ID)
    assert LinkCaseEvidenceBody(**base, source_record_id=RECORD_ID).source_record_id


def test_contradicting_evidence_is_as_linkable_as_supporting_evidence() -> None:
    """`contradicts` is first-class: it is how a case is later closed honestly (§3.6.3)."""
    assert "contradicts" in EVIDENCE_RELATIONS
    body = LinkCaseEvidenceBody(
        opportunity_id=CASE_ID, opportunity_version=1, relation="contradicts",
        source_record_id=RECORD_ID, note="dice que ya compraron",
    )
    assert validated_case(LINK_CASE_EVIDENCE, body)["relation"] == "contradicts"


def test_an_interest_needs_a_subject_and_a_positive_quantity() -> None:
    base = {"opportunity_id": CASE_ID, "opportunity_version": 1, "note": "x"}
    with pytest.raises(Exception):
        RecordCaseInterestBody(**base)
    with pytest.raises(Exception):
        RecordCaseInterestBody(**base, model_text="UP200Ht", quantity=Decimal("0"))
    with pytest.raises(Exception):
        RecordCaseInterestBody(**base, model_text="UP200Ht", quantity=Decimal("-1"))
    with pytest.raises(Exception):
        # A unit with no quantity describes nothing.
        RecordCaseInterestBody(**base, model_text="UP200Ht", quantity_unit="unidad")
    assert RecordCaseInterestBody(**base, model_text="UP200Ht", quantity=Decimal("2"),
                                  quantity_unit="unidad")


def test_the_supplier_exception_belongs_to_one_role_only() -> None:
    """An exception attached to `manufacturer` justifies a claim nobody made."""
    body = AddCaseOrganizationBody(
        opportunity_id=CASE_ID, opportunity_version=1, organization_id=ORGANIZATION_ID,
        organization_version=1, role="manufacturer",
        supplier_exception_reason="compra para su laboratorio", note="x",
    )
    with pytest.raises(CommandRefused) as excinfo:
        validated_case(ADD_CASE_ORGANIZATION, body)
    assert excinfo.value.code == "supplier_exception_is_only_for_a_requesting_institution"
    assert excinfo.value.status_code == 422


def test_a_role_has_no_default_and_mentioned_is_a_value() -> None:
    """Silence about what an institution is to a case is recordable, not implied."""
    assert AddCaseOrganizationBody.model_fields["role"].is_required()
    assert "mentioned" in CASE_ORGANIZATION_ROLES
    body = AddCaseOrganizationBody(
        opportunity_id=CASE_ID, opportunity_version=1, organization_id=ORGANIZATION_ID,
        organization_version=1, role="mentioned", note="aparece en el correo",
    )
    assert validated_case(ADD_CASE_ORGANIZATION, body)["role"] == "mentioned"


def test_won_is_refused_by_name_rather_than_by_a_constraint() -> None:
    body = AdvanceCaseStageBody(
        opportunity_id=CASE_ID, opportunity_version=1, stage="won", note="x"
    )
    with pytest.raises(CommandRefused) as excinfo:
        validated_case(ADVANCE_CASE_STAGE, body)
    assert excinfo.value.code == "won_requires_a_quote"


def test_closing_a_case_needs_a_motive_and_nothing_else_may_carry_one() -> None:
    for stage in ("lost", "abandoned"):
        with pytest.raises(CommandRefused) as excinfo:
            validated_case(
                ADVANCE_CASE_STAGE,
                AdvanceCaseStageBody(
                    opportunity_id=CASE_ID, opportunity_version=1, stage=stage, note="x"
                ),
            )
        assert excinfo.value.code == "closing_a_case_needs_a_motive"
    for stage in ("qualifying", "qualified", "quoting", "negotiating"):
        with pytest.raises(CommandRefused) as excinfo:
            validated_case(
                ADVANCE_CASE_STAGE,
                AdvanceCaseStageBody(
                    opportunity_id=CASE_ID, opportunity_version=1, stage=stage,
                    close_reason="se fue a otro", note="x",
                ),
            )
        assert excinfo.value.code == "close_reason_is_only_for_a_closing_stage"


def test_a_malformed_identifier_is_refused_by_name() -> None:
    body = LinkCaseEvidenceBody(
        opportunity_id="not-a-uuid", opportunity_version=1, relation="mentions",
        source_record_id=RECORD_ID, note="x",
    )
    with pytest.raises(CommandRefused) as excinfo:
        validated_case(LINK_CASE_EVIDENCE, body)
    assert excinfo.value.code == "malformed_identifier"


# ------------------------------------------------------------------ the stage table


def test_the_stage_table_is_the_one_in_the_document() -> None:
    """WORKFLOWS.md §1.1, transcribed once and checked here rather than trusted."""
    assert set(STAGE_TRANSITIONS) == set(CASE_STAGES)
    assert STAGE_TRANSITIONS["lead"] == ("qualifying", "abandoned", "lost")
    assert STAGE_TRANSITIONS["negotiating"] == ("won", "lost", "quoting", "abandoned")
    for terminal in ("won", "lost", "abandoned"):
        assert STAGE_TRANSITIONS[terminal] == ()


def test_a_terminal_stage_leads_nowhere_at_all() -> None:
    for terminal in ("won", "lost", "abandoned"):
        for target in CASE_STAGES:
            if target == terminal:
                continue
            assert not stage_transition_allowed(terminal, target), (terminal, target)


def test_the_stage_table_in_the_migration_is_the_same_table() -> None:
    """Read the SQL and compare it with the Python, pair by pair.

    The rule is stated twice on purpose — the command refuses by name, the database refuses
    where no code path can miss it — and two statements of one rule are two statements that
    can disagree. This is the test that notices.
    """
    import pathlib
    import re

    from v2_command_harness import MIGRATIONS

    sql = (MIGRATIONS / "20260922200000_slice3_commercial_case_commands.sql").read_text(
        encoding="utf-8"
    )
    guard = sql.partition("crm.opportunity_stage_guard() returns trigger")[2]
    assert guard, "the stage guard is missing from the migration"
    found: dict[str, tuple[str, ...]] = {}
    for stage, targets in re.findall(
        r"when '(\w+)'\s+then array\[([^\]]*)\]", guard
    ):
        found[stage] = tuple(re.findall(r"'(\w+)'", targets))
    for stage, targets in found.items():
        assert targets == STAGE_TRANSITIONS[stage], stage
    # Every non-terminal stage appears; the terminals are the SQL `else` branch.
    assert set(found) == {s for s in CASE_STAGES if STAGE_TRANSITIONS[s]}
    assert "else array[]::text[]" in guard
    assert str(pathlib.Path)  # keep the import honest


# --------------------------------------------------------- database-backed proofs (opt-in)

from v2_command_harness import (  # noqa: E402
    build_disposable_database,
    needs_db as _needs_db,
    runtime_dsn as _runtime_dsn,
)

#: Tables no command in this file may move. Counted before and after every database-backed
#: command, so "the case model does not touch marketing" is a measurement rather than a
#: reading of the source.
UNTOUCHABLE_TABLES: tuple[str, ...] = (
    "outbound.campaign",
    "outbound.campaign_recipient",
    "outbound.contact_control",
    "outbound.send_attempt",
    "crm.person",
    "crm.affiliation",
    "crm.organization_relationship",
    "crm.organization_domain",
    "crm.contact_point",
    "crm.quote",
    "crm.task",
    "crm.activity",
    "crm.opportunity_participant",
)


@pytest.fixture(scope="module")
def disposable_database():
    """This module's own `origenlab_test_<hex>`, migrated and dropped around it."""
    yield from build_disposable_database()


@pytest.fixture
def world(disposable_database):
    """A fictitious world: one operator, one document, three institutions, one product.

    Every value is invented and every domain is a reserved `.invalid` / `.test` name. This
    repository is public, and a fixture that happened to be a real institution would publish
    a commercial fact about it.
    """
    import psycopg

    tag = uuid.uuid4().hex[:12]
    state: dict[str, object] = {"tag": tag}
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        for key, role in (("operator_id", "sales"), ("other_operator_id", "sales")):
            cur.execute(
                """
                insert into platform.operator
                    (auth_user_id, email_norm, display_name, role, status)
                values (gen_random_uuid(), %s, 'Pytest Operator', %s, 'active')
                returning id::text
                """,
                (f"pytest-{key}-{tag}@example.test", role),
            )
            state[key] = cur.fetchone()[0]
        cur.execute(
            """
            insert into evidence.source_record (kind, dedupe_key, payload, source_uri)
            values ('gmail_message', %s, '{"subject": "pytest"}'::jsonb, %s)
            returning id::text
            """,
            (f"pytest-case:{tag}", f"gmail://msg/{tag}"),
        )
        state["source_record_id"] = cur.fetchone()[0]
        cur.execute(
            """
            insert into evidence.source_record
                (kind, dedupe_key, payload, is_quarantined, quarantine_reason, quarantined_at)
            values ('gmail_message', %s, '{}'::jsonb, true, %s, now())
            returning id::text
            """,
            (f"pytest-case-quarantined:{tag}", "la evidencia se contradice"),
        )
        state["quarantined_source_record_id"] = cur.fetchone()[0]
        cur.execute(
            """
            insert into evidence.assertion (source_record_id, kind, value_norm, value)
            values (%s, 'organization_name', %s, '{}'::jsonb)
            returning id::text
            """,
            (state["source_record_id"], f"instituto ficticio {tag}"),
        )
        state["assertion_id"] = cur.fetchone()[0]
        for key, kind, name in (
            ("university_id", "institution", f"Universidad Ficticia {tag}"),
            ("vendor_id", "company", f"Proveedor Ficticio {tag}"),
            ("agency_id", "institution", f"Central de Compras Ficticia {tag}"),
        ):
            cur.execute(
                "insert into crm.organization (kind, name, confirmation) "
                "values (%s, %s, 'confirmed') returning id::text, version",
                (kind, name),
            )
            row = cur.fetchone()
            state[key], state[f"{key}_version"] = row[0], row[1]
        # The vendor is a registered supplier *and* manufacturer, today, with no end date —
        # the shape the supplier exception exists for.
        cur.execute(
            "insert into crm.organization_relationship (organization_id, role, valid_from) "
            "values (%s, 'supplier', '2024-01-01'), (%s, 'manufacturer', '2024-01-01')",
            (state["vendor_id"], state["vendor_id"]),
        )
        cur.execute(
            "insert into catalog.product (manufacturer_organization_id, model_number) "
            "values (%s, %s) returning id::text",
            (state["vendor_id"], f"UP200Ht-{tag}"),
        )
        state["product_id"] = cur.fetchone()[0]
        cur.execute(
            "insert into comms.mailbox (address_norm) values (%s) returning id::text",
            (f"buzon-{tag}@example.invalid",),
        )
        mailbox_id = cur.fetchone()[0]
        cur.execute(
            "insert into comms.message (mailbox_id, provider_message_id, direction, "
            "internal_date) values (%s, %s, 'inbound', now()) returning id::text",
            (mailbox_id, f"msg-{tag}"),
        )
        state["message_id"] = cur.fetchone()[0]
        cur.execute(
            "insert into procurement.notice (codigo_externo, head) values (%s, '{}'::jsonb) "
            "returning id::text",
            (f"ID-PYTEST-{tag}",),
        )
        state["notice_id"] = cur.fetchone()[0]
    return state


def _repo(disposable_database: str):
    import psycopg

    from origenlab_api.v2.case_command_repository import V2CaseCommandRepository

    return V2CaseCommandRepository(psycopg.connect, _runtime_dsn(disposable_database))


def _identity(world, key: str = "operator_id") -> OperatorIdentity:
    return OperatorIdentity(
        operator_id=world[key],
        email_norm="pytest@example.test",
        display_name="Pytest Operator",
        role="sales",
        status="active",
    )


_KEYS = iter(range(1, 1_000_000))


def _run(repo, command_name, body, world, *, key: str | None = None, operator="operator_id"):
    """Run a real command through the real validator, as the unprivileged runtime role."""
    return repo.execute(
        command_name=command_name,
        operator=_identity(world, operator),
        fields=validated_case(command_name, body),
        idempotency_key=key or f"pytest-{next(_KEYS)}",
        digest=request_digest(command_name, body),
    )


def _counts(dsn: str, tables: tuple[str, ...] = UNTOUCHABLE_TABLES) -> dict[str, int]:
    import psycopg

    counted: dict[str, int] = {}
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        for table in tables:
            cur.execute(f"select count(*) from {table}")  # noqa: S608 - module constant
            counted[table] = cur.fetchone()[0]
    return counted


def _open_case(repo, world, title: str = "Caso de prueba") -> tuple[str, int]:
    result = _run(
        repo,
        OPEN_COMMERCIAL_CASE,
        OpenCommercialCaseBody(
            title=title,
            origin_source_record_id=world["source_record_id"],
            note="correo entrante",
        ),
        world,
    )
    return result["opportunity_id"], result["opportunity_version"]


@_needs_db
def test_the_tests_never_run_against_a_development_database(disposable_database) -> None:
    """The guard that makes every other database-backed test in this file safe."""
    for dsn in (disposable_database, _runtime_dsn(disposable_database)):
        database = dsn.rpartition("/")[2]
        assert database.startswith("origenlab_test_")
        assert database not in {"origenlab_dev", "origenlab_clean"}


@_needs_db
def test_opening_a_case_writes_the_case_and_its_origin_together(
    disposable_database, world
) -> None:
    import psycopg

    repo = _repo(disposable_database)
    result = _run(
        repo,
        OPEN_COMMERCIAL_CASE,
        OpenCommercialCaseBody(
            title="Homogeneizador para laboratorio",
            origin_source_record_id=world["source_record_id"],
            note="pide cotización",
        ),
        world,
    )
    assert result["stage"] == "lead"
    assert result["organization_id"] is None
    assert result["created"] == ["opportunity", "opportunity_evidence"]
    assert len(result["event_ids"]) == 2

    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute(
            "select stage, organization_id, origin_source_record_id::text, version, "
            "owner_operator_id::text from crm.opportunity where id = %s",
            (result["opportunity_id"],),
        )
        stage, organization_id, origin, version, owner = cur.fetchone()
        assert (stage, organization_id, version) == ("lead", None, 1)
        assert origin == world["source_record_id"]
        assert owner == world["operator_id"], "the owner is the operator, never the body"
        cur.execute(
            "select relation, source_record_id::text, linked_by_operator_id::text "
            "from crm.opportunity_evidence where opportunity_id = %s",
            (result["opportunity_id"],),
        )
        assert cur.fetchall() == [
            ("origin", world["source_record_id"], world["operator_id"])
        ]
        cur.execute(
            "select event_type, actor_kind from crm.domain_event "
            "where command_receipt_id = %s order by stream_position",
            (result["command_receipt_id"],),
        )
        assert cur.fetchall() == [
            ("opportunity.created", "operator"),
            ("case_evidence.linked", "operator"),
        ]


@_needs_db
def test_a_case_cannot_be_opened_on_a_document_that_does_not_exist(
    disposable_database, world
) -> None:
    repo = _repo(disposable_database)
    with pytest.raises(CommandRefused) as excinfo:
        _run(
            repo,
            OPEN_COMMERCIAL_CASE,
            OpenCommercialCaseBody(
                title="Caso sin causa",
                origin_source_record_id=str(uuid.uuid4()),
                note="x",
            ),
            world,
        )
    assert excinfo.value.code == "source_record_not_found"
    assert excinfo.value.status_code == 404


@_needs_db
def test_a_case_cannot_be_opened_on_contradictory_evidence(disposable_database, world) -> None:
    repo = _repo(disposable_database)
    with pytest.raises(CommandRefused) as excinfo:
        _run(
            repo,
            OPEN_COMMERCIAL_CASE,
            OpenCommercialCaseBody(
                title="Caso sobre evidencia en cuarentena",
                origin_source_record_id=world["quarantined_source_record_id"],
                note="x",
            ),
            world,
        )
    assert excinfo.value.code == "source_record_quarantined"


@_needs_db
def test_a_refused_command_writes_nothing_at_all(disposable_database, world) -> None:
    """Not the row, not the event, not even its own receipt.

    The failure is forced in the *second* half of a two-write command: the case has a
    requesting institution already, so opening a second one is refused after the command has
    already locked the case and read the organization. A partial success would leave a role
    row with no matching `organization_id`, or a receipt claiming a decision nobody took.
    """
    import psycopg

    repo = _repo(disposable_database)
    case_id, version = _open_case(repo, world, "Caso para el rechazo")
    result = _run(
        repo,
        ADD_CASE_ORGANIZATION,
        AddCaseOrganizationBody(
            opportunity_id=case_id, opportunity_version=version,
            organization_id=world["university_id"],
            organization_version=world["university_id_version"],
            role="requesting_institution", note="firma el correo",
        ),
        world,
    )
    version = result["opportunity_version"]

    def snapshot() -> tuple:
        with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
            cur.execute(
                """
                select (select count(*) from crm.opportunity_organization),
                       (select count(*) from crm.domain_event),
                       (select count(*) from platform.command_receipt),
                       (select organization_id::text from crm.opportunity where id = %s),
                       (select version from crm.opportunity where id = %s)
                """,
                (case_id, case_id),
            )
            return cur.fetchone()

    before = snapshot()
    with pytest.raises(CommandRefused) as excinfo:
        _run(
            repo,
            ADD_CASE_ORGANIZATION,
            AddCaseOrganizationBody(
                opportunity_id=case_id, opportunity_version=version,
                organization_id=world["agency_id"],
                organization_version=world["agency_id_version"],
                role="requesting_institution", note="una segunda solicitante",
            ),
            world,
            key="rollback-key",
        )
    assert excinfo.value.code == "case_already_has_a_requesting_institution"
    assert snapshot() == before

    # And the key is still free, because the receipt rolled back with everything else.
    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute(
            "select count(*) from platform.command_receipt where idempotency_key = %s",
            ("rollback-key",),
        )
        assert cur.fetchone()[0] == 0


@_needs_db
def test_opening_a_case_is_atomic_when_the_second_half_fails(
    disposable_database, world
) -> None:
    """Force the origin link to fail and assert the case it would have created does not exist.

    The link fails because the same record is already this case's origin — which cannot
    happen through the command, so it is arranged directly: the point is not the refusal, it
    is that the `crm.opportunity` row written a statement earlier is gone afterwards.
    """
    import psycopg

    from origenlab_api.v2.case_command_repository import V2CaseCommandRepository

    class _FailsAfterTheCase(V2CaseCommandRepository):
        def _link_evidence_row(self, *args, **kwargs):
            raise CommandRefused(409, "forced", "the second half fails on purpose")

    repo = _FailsAfterTheCase(psycopg.connect, _runtime_dsn(disposable_database))
    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from crm.opportunity")
        before = cur.fetchone()[0]

    with pytest.raises(CommandRefused) as excinfo:
        _run(
            repo,
            OPEN_COMMERCIAL_CASE,
            OpenCommercialCaseBody(
                title="Caso que no debe existir",
                origin_source_record_id=world["source_record_id"],
                note="x",
            ),
            world,
            key="atomic-open-key",
        )
    assert excinfo.value.code == "forced"

    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from crm.opportunity")
        assert cur.fetchone()[0] == before
        cur.execute(
            "select count(*) from crm.opportunity where title = %s",
            ("Caso que no debe existir",),
        )
        assert cur.fetchone()[0] == 0
        cur.execute(
            "select count(*) from platform.command_receipt where idempotency_key = %s",
            ("atomic-open-key",),
        )
        assert cur.fetchone()[0] == 0


@_needs_db
def test_the_requesting_institution_and_the_case_column_move_together(
    disposable_database, world
) -> None:
    import psycopg

    repo = _repo(disposable_database)
    case_id, version = _open_case(repo, world, "Caso con solicitante")
    result = _run(
        repo,
        ADD_CASE_ORGANIZATION,
        AddCaseOrganizationBody(
            opportunity_id=case_id, opportunity_version=version,
            organization_id=world["university_id"],
            organization_version=world["university_id_version"],
            role="requesting_institution", note="firma el correo",
        ),
        world,
    )
    assert result["opportunity_version"] == version + 1
    assert result["supplier_exception"] is False

    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute(
            "select organization_id::text from crm.opportunity where id = %s", (case_id,)
        )
        assert cur.fetchone()[0] == world["university_id"]
        cur.execute(
            "select role, confirmation, confirmed_by_operator_id::text, valid_to "
            "from crm.opportunity_organization where id = %s",
            (result["opportunity_organization_id"],),
        )
        assert cur.fetchone() == (
            "requesting_institution", "confirmed", world["operator_id"], None,
        )
        cur.execute(
            "select event_type from crm.domain_event where command_receipt_id = %s "
            "order by stream_position",
            (result["command_receipt_id"],),
        )
        assert [r[0] for r in cur.fetchall()] == [
            "case_organization.added",
            "opportunity.organization_set",
        ]


@_needs_db
def test_one_institution_may_hold_several_parts_on_one_case(disposable_database, world) -> None:
    """A distributor is legitimately both supplier and manufacturer (§3.6.1)."""
    repo = _repo(disposable_database)
    case_id, version = _open_case(repo, world, "Caso con un distribuidor")
    for role in ("supplier", "manufacturer", "mentioned"):
        result = _run(
            repo,
            ADD_CASE_ORGANIZATION,
            AddCaseOrganizationBody(
                opportunity_id=case_id, opportunity_version=version,
                organization_id=world["vendor_id"],
                organization_version=world["vendor_id_version"],
                role=role, note=f"es {role}",
            ),
            world,
        )
        assert result["role"] == role
        # None of these is the requester, so the case row itself never moved.
        assert result["opportunity_version"] == version

    with pytest.raises(CommandRefused) as excinfo:
        _run(
            repo,
            ADD_CASE_ORGANIZATION,
            AddCaseOrganizationBody(
                opportunity_id=case_id, opportunity_version=version,
                organization_id=world["vendor_id"],
                organization_version=world["vendor_id_version"],
                role="supplier", note="otra vez",
            ),
            world,
        )
    assert excinfo.value.code == "case_organization_role_already_current"


@_needs_db
def test_a_supplier_is_never_made_the_customer_without_a_written_exception(
    disposable_database, world
) -> None:
    """The Hielscher case, with a fictitious vendor (§3.6.1, `m-dom-supplier-never-customer`)."""
    import psycopg

    repo = _repo(disposable_database)
    case_id, version = _open_case(repo, world, "El proveedor pide para su propio laboratorio")

    with pytest.raises(CommandRefused) as excinfo:
        _run(
            repo,
            ADD_CASE_ORGANIZATION,
            AddCaseOrganizationBody(
                opportunity_id=case_id, opportunity_version=version,
                organization_id=world["vendor_id"],
                organization_version=world["vendor_id_version"],
                role="requesting_institution", note="sin justificar",
            ),
            world,
        )
    assert excinfo.value.code == "supplier_exception_required"
    assert excinfo.value.status_code == 422

    result = _run(
        repo,
        ADD_CASE_ORGANIZATION,
        AddCaseOrganizationBody(
            opportunity_id=case_id, opportunity_version=version,
            organization_id=world["vendor_id"],
            organization_version=world["vendor_id_version"],
            role="requesting_institution",
            supplier_exception_reason="compra un equipo para su propio laboratorio interno",
            note="excepción justificada",
        ),
        world,
    )
    assert result["supplier_exception"] is True

    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute(
            "select supplier_exception_by_operator_id::text, supplier_exception_reason, "
            "supplier_exception_at is not null from crm.opportunity_organization where id = %s",
            (result["opportunity_organization_id"],),
        )
        operator_id, reason, has_timestamp = cur.fetchone()
        assert operator_id == world["operator_id"]
        assert reason == "compra un equipo para su propio laboratorio interno"
        assert has_timestamp
        # Both readings stay visible: the exception never edits the relationship.
        cur.execute(
            "select count(*) from crm.organization_relationship where organization_id = %s "
            "and valid_to is null",
            (world["vendor_id"],),
        )
        assert cur.fetchone()[0] == 2


@_needs_db
def test_an_exception_nobody_needed_is_refused(disposable_database, world) -> None:
    """Recording one would read later as a supplier we sold to."""
    repo = _repo(disposable_database)
    case_id, version = _open_case(repo, world, "Caso sin proveedor")
    with pytest.raises(CommandRefused) as excinfo:
        _run(
            repo,
            ADD_CASE_ORGANIZATION,
            AddCaseOrganizationBody(
                opportunity_id=case_id, opportunity_version=version,
                organization_id=world["university_id"],
                organization_version=world["university_id_version"],
                role="requesting_institution",
                supplier_exception_reason="no hace falta", note="x",
            ),
            world,
        )
    assert excinfo.value.code == "supplier_exception_is_not_needed"


@_needs_db
def test_the_supplier_exception_can_never_be_rewritten(disposable_database, world) -> None:
    """The trigger of §3.6.1, reached directly — no command offers this, and none may."""
    import psycopg

    repo = _repo(disposable_database)
    case_id, version = _open_case(repo, world, "Caso con excepción")
    result = _run(
        repo,
        ADD_CASE_ORGANIZATION,
        AddCaseOrganizationBody(
            opportunity_id=case_id, opportunity_version=version,
            organization_id=world["vendor_id"],
            organization_version=world["vendor_id_version"],
            role="requesting_institution",
            supplier_exception_reason="motivo original", note="x",
        ),
        world,
    )
    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute(
                "update crm.opportunity_organization set supplier_exception_reason = %s "
                "where id = %s",
                ("otro motivo", result["opportunity_organization_id"]),
            )


@_needs_db
def test_changing_a_part_closes_one_reading_and_opens_another(
    disposable_database, world
) -> None:
    """Nothing is edited, and a same-day correction is possible (§3.6.1)."""
    import psycopg

    repo = _repo(disposable_database)
    case_id, version = _open_case(repo, world, "Caso que corrige un papel")
    added = _run(
        repo,
        ADD_CASE_ORGANIZATION,
        AddCaseOrganizationBody(
            opportunity_id=case_id, opportunity_version=version,
            organization_id=world["university_id"],
            organization_version=world["university_id_version"],
            role="requesting_institution", note="parecía la solicitante",
        ),
        world,
    )
    version = added["opportunity_version"]

    changed = _run(
        repo,
        SET_CASE_ORGANIZATION_ROLE,
        SetCaseOrganizationRoleBody(
            opportunity_id=case_id, opportunity_version=version,
            opportunity_organization_id=added["opportunity_organization_id"],
            role="end_user_institution",
            note="en realidad el equipo es para ellos, compra la central",
        ),
        world,
    )
    assert changed["previous_role"] == "requesting_institution"
    assert changed["role"] == "end_user_institution"
    assert changed["closed_opportunity_organization_id"] == added["opportunity_organization_id"]

    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute(
            "select role, valid_from, valid_to from crm.opportunity_organization "
            "where opportunity_id = %s order by role",
            (case_id,),
        )
        rows = cur.fetchall()
        assert [r[0] for r in rows] == ["end_user_institution", "requesting_institution"]
        # The old reading is closed the same day it was opened, and is still readable.
        closed = rows[1]
        assert closed[2] is not None and closed[2] == closed[1]
        # The case no longer claims a customer.
        cur.execute(
            "select organization_id from crm.opportunity where id = %s", (case_id,)
        )
        assert cur.fetchone()[0] is None
        cur.execute(
            "select event_type from crm.domain_event where command_receipt_id = %s "
            "order by stream_position",
            (changed["command_receipt_id"],),
        )
        assert [r[0] for r in cur.fetchall()] == [
            "case_organization.ended",
            "opportunity.organization_set",
            "case_organization.added",
        ]


@_needs_db
def test_confirming_a_machine_proposal_moves_only_who_vouches_for_it(
    disposable_database, world
) -> None:
    """The only in-place move. The reading did not change; its author did."""
    import psycopg

    repo = _repo(disposable_database)
    case_id, version = _open_case(repo, world, "Caso con una propuesta de la máquina")

    # A machine may only ever propose `mentioned`, and only the owner can write such a row:
    # no command in this boundary can, which is the point.
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            """
            insert into crm.opportunity_organization
                (opportunity_id, organization_id, role, valid_from, confirmation)
            values (%s, %s, 'mentioned', current_date, 'machine_proposed')
            returning id::text
            """,
            (case_id, world["agency_id"]),
        )
        proposal_id = cur.fetchone()[0]

    result = _run(
        repo,
        SET_CASE_ORGANIZATION_ROLE,
        SetCaseOrganizationRoleBody(
            opportunity_id=case_id, opportunity_version=version,
            opportunity_organization_id=proposal_id, role="mentioned",
            note="sí, aparece nombrada y todavía no sé por qué",
        ),
        world,
    )
    assert result["created"] == []
    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute(
            "select confirmation, confirmed_by_operator_id::text, role "
            "from crm.opportunity_organization where id = %s",
            (proposal_id,),
        )
        assert cur.fetchone() == ("confirmed", world["operator_id"], "mentioned")
        cur.execute(
            "select event_type from crm.domain_event where command_receipt_id = %s",
            (result["command_receipt_id"],),
        )
        assert [r[0] for r in cur.fetchall()] == ["case_organization.role_confirmed"]

    # Confirming it twice is refused: it would write an event saying a decision was taken.
    with pytest.raises(CommandRefused) as excinfo:
        _run(
            repo,
            SET_CASE_ORGANIZATION_ROLE,
            SetCaseOrganizationRoleBody(
                opportunity_id=case_id, opportunity_version=version,
                opportunity_organization_id=proposal_id, role="mentioned", note="otra vez",
            ),
            world,
        )
    assert excinfo.value.code == "case_organization_role_unchanged"


@_needs_db
def test_a_part_on_another_case_is_refused(disposable_database, world) -> None:
    repo = _repo(disposable_database)
    first, first_version = _open_case(repo, world, "Caso uno")
    second, second_version = _open_case(repo, world, "Caso dos")
    added = _run(
        repo,
        ADD_CASE_ORGANIZATION,
        AddCaseOrganizationBody(
            opportunity_id=first, opportunity_version=first_version,
            organization_id=world["agency_id"],
            organization_version=world["agency_id_version"],
            role="purchasing_agent", note="x",
        ),
        world,
    )
    with pytest.raises(CommandRefused) as excinfo:
        _run(
            repo,
            SET_CASE_ORGANIZATION_ROLE,
            SetCaseOrganizationRoleBody(
                opportunity_id=second, opportunity_version=second_version,
                opportunity_organization_id=added["opportunity_organization_id"],
                role="mentioned", note="x",
            ),
            world,
        )
    assert excinfo.value.code == "case_organization_belongs_to_another_case"


@_needs_db
def test_an_interest_records_a_quantity_and_never_a_price(disposable_database, world) -> None:
    import psycopg

    repo = _repo(disposable_database)
    case_id, version = _open_case(repo, world, "Caso con un interés")
    result = _run(
        repo,
        RECORD_CASE_INTEREST,
        RecordCaseInterestBody(
            opportunity_id=case_id, opportunity_version=version,
            product_id=world["product_id"],
            manufacturer_organization_id=world["vendor_id"],
            model_text="UP200Ht", quantity=Decimal("2"), quantity_unit="unidad",
            description="homogeneizador ultrasónico",
            note="lo pide por su nombre",
        ),
        world,
    )
    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute(
            "select confirmation, confirmed_by_operator_id::text, quantity, quantity_unit, "
            "withdrawn_at from crm.opportunity_interest where id = %s",
            (result["opportunity_interest_id"],),
        )
        confirmation, operator_id, quantity, unit, withdrawn = cur.fetchone()
        assert (confirmation, operator_id, unit, withdrawn) == (
            "confirmed", world["operator_id"], "unidad", None,
        )
        assert quantity == Decimal("2.000")
        # The table itself has no money column, whatever any command might want to write.
        cur.execute(
            "select column_name from information_schema.columns where table_schema = 'crm' "
            "and table_name = 'opportunity_interest'"
        )
        columns = {r[0] for r in cur.fetchall()}
        assert not (columns & {"price", "amount", "currency", "total", "margin", "unit_price"})


@_needs_db
def test_an_interest_may_not_contradict_the_catalogue(disposable_database, world) -> None:
    repo = _repo(disposable_database)
    case_id, version = _open_case(repo, world, "Caso con un fabricante equivocado")
    with pytest.raises(CommandRefused) as excinfo:
        _run(
            repo,
            RECORD_CASE_INTEREST,
            RecordCaseInterestBody(
                opportunity_id=case_id, opportunity_version=version,
                product_id=world["product_id"],
                manufacturer_organization_id=world["university_id"],
                note="x",
            ),
            world,
        )
    assert excinfo.value.code == "interest_manufacturer_disagrees_with_the_product"


@_needs_db
@pytest.mark.parametrize(
    "subject", ["source_record_id", "assertion_id", "message_id", "notice_id"]
)
def test_every_typed_subject_can_be_linked(disposable_database, world, subject: str) -> None:
    repo = _repo(disposable_database)
    case_id, version = _open_case(repo, world, f"Caso con evidencia {subject}")
    result = _run(
        repo,
        LINK_CASE_EVIDENCE,
        LinkCaseEvidenceBody(
            opportunity_id=case_id, opportunity_version=version,
            relation="mentions", note="lo menciona",
            **{subject: world[subject]},
        ),
        world,
    )
    assert result["subject_kind"] == subject.removesuffix("_id")
    assert result["subject_id"] == world[subject]


@_needs_db
def test_evidence_that_does_not_exist_is_a_404_not_a_new_row(
    disposable_database, world
) -> None:
    repo = _repo(disposable_database)
    case_id, version = _open_case(repo, world, "Caso con evidencia inexistente")
    with pytest.raises(CommandRefused) as excinfo:
        _run(
            repo,
            LINK_CASE_EVIDENCE,
            LinkCaseEvidenceBody(
                opportunity_id=case_id, opportunity_version=version,
                relation="mentions", message_id=str(uuid.uuid4()), note="x",
            ),
            world,
        )
    assert excinfo.value.code == "evidence_subject_not_found"
    assert excinfo.value.status_code == 404


@_needs_db
def test_the_same_reading_of_the_same_document_is_recorded_once(
    disposable_database, world
) -> None:
    """...but a *different* reading of it is a different fact, and is allowed."""
    repo = _repo(disposable_database)
    case_id, version = _open_case(repo, world, "Caso con evidencia repetida")
    _run(
        repo,
        LINK_CASE_EVIDENCE,
        LinkCaseEvidenceBody(
            opportunity_id=case_id, opportunity_version=version,
            relation="supports_interest", message_id=world["message_id"], note="x",
        ),
        world,
    )
    with pytest.raises(CommandRefused) as excinfo:
        _run(
            repo,
            LINK_CASE_EVIDENCE,
            LinkCaseEvidenceBody(
                opportunity_id=case_id, opportunity_version=version,
                relation="supports_interest", message_id=world["message_id"], note="otra vez",
            ),
            world,
        )
    assert excinfo.value.code == "evidence_already_linked"

    contradiction = _run(
        repo,
        LINK_CASE_EVIDENCE,
        LinkCaseEvidenceBody(
            opportunity_id=case_id, opportunity_version=version,
            relation="contradicts", message_id=world["message_id"],
            note="el mismo correo dice que ya compraron",
        ),
        world,
    )
    assert contradiction["relation"] == "contradicts"


@_needs_db
def test_a_case_may_not_reach_qualified_without_a_confirmed_requester(
    disposable_database, world
) -> None:
    repo = _repo(disposable_database)
    case_id, version = _open_case(repo, world, "Caso sin solicitante")
    moved = _run(
        repo,
        ADVANCE_CASE_STAGE,
        AdvanceCaseStageBody(
            opportunity_id=case_id, opportunity_version=version, stage="qualifying",
            note="sigo averiguando quién pide",
        ),
        world,
    )
    version = moved["opportunity_version"]
    with pytest.raises(CommandRefused) as excinfo:
        _run(
            repo,
            ADVANCE_CASE_STAGE,
            AdvanceCaseStageBody(
                opportunity_id=case_id, opportunity_version=version, stage="qualified",
                note="x",
            ),
            world,
        )
    assert excinfo.value.code == "stage_requires_a_confirmed_requesting_institution"


@_needs_db
def test_a_case_that_never_found_its_requester_can_still_be_abandoned(
    disposable_database, world
) -> None:
    """The defect this slice found: `lost` and `abandoned` are reachable from `lead`.

    `opportunity_organization_required_from_qualified` used to catch them as well, so a case
    with no institution could be opened and then never closed — the only reachable states
    were `lead` and `qualifying`, forever.
    """
    import psycopg

    repo = _repo(disposable_database)
    case_id, version = _open_case(repo, world, "Caso que nunca supo quién pedía")
    result = _run(
        repo,
        ADVANCE_CASE_STAGE,
        AdvanceCaseStageBody(
            opportunity_id=case_id, opportunity_version=version, stage="abandoned",
            close_reason="nunca respondieron y no sé qué institución era",
            note="cierre",
        ),
        world,
    )
    assert result["closed"] is True
    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute(
            "select stage, organization_id, closed_at is not null, close_reason "
            "from crm.opportunity where id = %s",
            (case_id,),
        )
        stage, organization_id, closed, reason = cur.fetchone()
        assert (stage, organization_id, closed) == ("abandoned", None, True)
        assert reason.startswith("nunca respondieron")


@_needs_db
def test_a_terminal_case_is_never_revived_and_never_edited(
    disposable_database, world
) -> None:
    repo = _repo(disposable_database)
    case_id, version = _open_case(repo, world, "Caso cerrado")
    closed = _run(
        repo,
        ADVANCE_CASE_STAGE,
        AdvanceCaseStageBody(
            opportunity_id=case_id, opportunity_version=version, stage="lost",
            close_reason="compraron a otro proveedor", note="cierre",
        ),
        world,
    )
    version = closed["opportunity_version"]

    for command, body in (
        (
            ADVANCE_CASE_STAGE,
            AdvanceCaseStageBody(
                opportunity_id=case_id, opportunity_version=version, stage="lead", note="x"
            ),
        ),
        (
            RECORD_CASE_INTEREST,
            RecordCaseInterestBody(
                opportunity_id=case_id, opportunity_version=version,
                model_text="UP200Ht", note="x",
            ),
        ),
        (
            LINK_CASE_EVIDENCE,
            LinkCaseEvidenceBody(
                opportunity_id=case_id, opportunity_version=version,
                relation="mentions", message_id=world["message_id"], note="x",
            ),
        ),
        (
            ADD_CASE_ORGANIZATION,
            AddCaseOrganizationBody(
                opportunity_id=case_id, opportunity_version=version,
                organization_id=world["agency_id"],
                organization_version=world["agency_id_version"],
                role="mentioned", note="x",
            ),
        ),
    ):
        with pytest.raises(CommandRefused) as excinfo:
            _run(repo, command, body, world)
        assert excinfo.value.code == "case_is_closed", command


@_needs_db
def test_the_database_refuses_an_illegal_transition_even_without_the_command(
    disposable_database, world
) -> None:
    """The command is not the only guard, and the owner is not above the stage machine.

    Run as `origenlab_owner`, which crosses RLS by ownership and holds every privilege. A rule
    that role cannot break is a rule no runtime role, no future second writer and no
    hand-typed UPDATE can break either.
    """
    import psycopg

    repo = _repo(disposable_database)
    case_id, _ = _open_case(repo, world, "Caso contra el disparador")
    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        for bad in ("qualified", "quoting", "negotiating", "won"):
            with pytest.raises(psycopg.errors.RaiseException):
                cur.execute(
                    "update crm.opportunity set stage = %s where id = %s", (bad, case_id)
                )
            conn.rollback()
            cur.execute("set role origenlab_owner")
        # A case is born at `lead`, so an INSERT straight into a later stage is refused too —
        # otherwise the whole transition table could be walked around in one statement.
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute(
                "insert into crm.opportunity (title, stage, owner_operator_id) "
                "values ('directo a negociación', 'negotiating', %s)",
                (world["operator_id"],),
            )
        conn.rollback()
        cur.execute("set role origenlab_owner")
        # And a case is never deleted, only closed with a stage and a motive.
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute("delete from crm.opportunity where id = %s", (case_id,))


@_needs_db
def test_two_operators_racing_one_case_do_not_both_win(disposable_database, world) -> None:
    """The loser gets a 409 naming the conflict, which is the honest answer."""
    repo = _repo(disposable_database)
    case_id, version = _open_case(repo, world, "Caso en disputa")

    first = _run(
        repo,
        ADVANCE_CASE_STAGE,
        AdvanceCaseStageBody(
            opportunity_id=case_id, opportunity_version=version, stage="qualifying",
            note="yo primero",
        ),
        world,
    )
    assert first["opportunity_version"] == version + 1

    # The second operator was looking at the version the first one moved off.
    with pytest.raises(CommandRefused) as excinfo:
        _run(
            repo,
            ADVANCE_CASE_STAGE,
            AdvanceCaseStageBody(
                opportunity_id=case_id, opportunity_version=version, stage="abandoned",
                close_reason="creo que no va", note="yo segundo",
            ),
            world,
            operator="other_operator_id",
        )
    assert excinfo.value.code == "case_version_conflict"
    assert excinfo.value.status_code == 409


@_needs_db
def test_two_concurrent_commands_on_one_case_cannot_both_commit(
    disposable_database, world
) -> None:
    """Not a stale read — two live transactions, overlapping, on the same case row.

    The first holds the case's row lock; the second blocks on it, and when it is let through
    the version it compared against is gone. One of them is refused, and it is the one that
    decided second.
    """
    import threading

    repo = _repo(disposable_database)
    case_id, version = _open_case(repo, world, "Caso con dos transacciones")

    barrier = threading.Barrier(2, timeout=20)
    outcomes: list[tuple[str, object]] = []
    lock = threading.Lock()

    def decide(tag: str, stage: str, operator: str) -> None:
        body = AdvanceCaseStageBody(
            opportunity_id=case_id, opportunity_version=version, stage=stage,
            note=f"decisión {tag}",
        )
        barrier.wait()
        try:
            result = _run(repo, ADVANCE_CASE_STAGE, body, world, operator=operator)
            with lock:
                outcomes.append((tag, result["stage"]))
        except CommandRefused as exc:
            with lock:
                outcomes.append((tag, exc.code))

    threads = [
        threading.Thread(target=decide, args=("a", "qualifying", "operator_id")),
        threading.Thread(target=decide, args=("b", "qualifying", "other_operator_id")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(outcomes) == 2
    results = sorted(value for _, value in outcomes)
    assert results == ["case_version_conflict", "qualifying"], outcomes


@_needs_db
def test_a_command_replays_instead_of_deciding_twice(disposable_database, world) -> None:
    import psycopg

    repo = _repo(disposable_database)
    case_id, version = _open_case(repo, world, "Caso que se repite")
    body = RecordCaseInterestBody(
        opportunity_id=case_id, opportunity_version=version, model_text="UP200Ht",
        note="lo pide por su nombre",
    )
    first = _run(repo, RECORD_CASE_INTEREST, body, world, key="replay-key")
    second = _run(repo, RECORD_CASE_INTEREST, body, world, key="replay-key")
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert second["opportunity_interest_id"] == first["opportunity_interest_id"]

    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute(
            "select count(*) from crm.opportunity_interest where opportunity_id = %s",
            (case_id,),
        )
        assert cur.fetchone()[0] == 1


@_needs_db
def test_one_key_for_a_different_request_is_refused(disposable_database, world) -> None:
    repo = _repo(disposable_database)
    case_id, version = _open_case(repo, world, "Caso con una llave reutilizada")
    _run(
        repo,
        RECORD_CASE_INTEREST,
        RecordCaseInterestBody(
            opportunity_id=case_id, opportunity_version=version, model_text="UP200Ht",
            note="primero",
        ),
        world,
        key="reused-key",
    )
    with pytest.raises(CommandRefused) as excinfo:
        _run(
            repo,
            RECORD_CASE_INTEREST,
            RecordCaseInterestBody(
                opportunity_id=case_id, opportunity_version=version, model_text="OTRO",
                note="segundo",
            ),
            world,
            key="reused-key",
        )
    assert excinfo.value.code == "idempotency_key_reused"


@_needs_db
def test_no_case_command_moves_a_marketing_row_or_creates_a_person(
    disposable_database, world
) -> None:
    """Counted, not read. Every command in the vocabulary runs, and nothing listed moves."""
    repo = _repo(disposable_database)
    before = _counts(disposable_database)

    case_id, version = _open_case(repo, world, "Caso que no toca nada de marketing")
    added = _run(
        repo,
        ADD_CASE_ORGANIZATION,
        AddCaseOrganizationBody(
            opportunity_id=case_id, opportunity_version=version,
            organization_id=world["university_id"],
            organization_version=world["university_id_version"],
            role="requesting_institution", note="firma el correo",
        ),
        world,
    )
    version = added["opportunity_version"]
    _run(
        repo,
        SET_CASE_ORGANIZATION_ROLE,
        SetCaseOrganizationRoleBody(
            opportunity_id=case_id, opportunity_version=version,
            opportunity_organization_id=added["opportunity_organization_id"],
            role="end_user_institution", note="corrijo",
        ),
        world,
    )
    version += 1
    _run(
        repo,
        RECORD_CASE_INTEREST,
        RecordCaseInterestBody(
            opportunity_id=case_id, opportunity_version=version, model_text="UP200Ht",
            note="busca esto",
        ),
        world,
    )
    _run(
        repo,
        LINK_CASE_EVIDENCE,
        LinkCaseEvidenceBody(
            opportunity_id=case_id, opportunity_version=version, relation="mentions",
            notice_id=world["notice_id"], note="hay una licitación parecida",
        ),
        world,
    )
    _run(
        repo,
        ADVANCE_CASE_STAGE,
        AdvanceCaseStageBody(
            opportunity_id=case_id, opportunity_version=version, stage="qualifying",
            note="avanzo",
        ),
        world,
    )
    assert _counts(disposable_database) == before
