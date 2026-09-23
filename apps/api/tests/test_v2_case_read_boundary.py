"""Tests for the V2 commercial-case read boundary — `GET /v2/cases` and `GET /v2/cases/{id}`.

Two layers, the same split the rest of the V2 suite makes.

**In process, always run.** That the two routes are GET and nothing else, that a malformed
identifier is a 404 rather than a 500, that an unknown stage is a 422 rather than an empty
page, and that the card attaches the command boundary's own stage table rather than a copy.

**Database-backed, opt-in.** What a case actually reads back as. These build a case by
running the **real commands** through the real validator, then read it through the read
repository — so the test proves the two halves agree rather than asserting a query against
rows a test inserted in whatever shape made the query pass. They skip without
`ORIGENLAB_V2_TEST_DSN` and `ORIGENLAB_V2_API_TEST_DSN`.

Every fixture is invented, and every domain is a reserved `.invalid` / `.test` name. This
repository is public; no real institution's commercial situation is described here. The
database is created and dropped by the harness, so the clean room and `origenlab_dev` are
unreachable by construction rather than by a cleanup convention.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from origenlab_api.v2 import routes
from origenlab_api.v2.case_commands import (
    ADD_CASE_ORGANIZATION,
    LINK_CASE_EVIDENCE,
    OPEN_COMMERCIAL_CASE,
    RECORD_CASE_INTEREST,
    SET_CASE_ORGANIZATION_ROLE,
    STAGE_TRANSITIONS,
    AddCaseOrganizationBody,
    LinkCaseEvidenceBody,
    OpenCommercialCaseBody,
    RecordCaseInterestBody,
    SetCaseOrganizationRoleBody,
    validated_case,
)
from origenlab_api.v2.commands import request_digest
from origenlab_api.v2.identity import OperatorIdentity

# ------------------------------------------------------------------ in process


def test_the_case_routes_are_get_and_nothing_else() -> None:
    """The read router stays a read router. A POST here would be a second writer."""
    case_routes = [r for r in routes.router.routes if "/cases" in getattr(r, "path", "")]
    assert len(case_routes) == 2
    for route in case_routes:
        assert set(route.methods) == {"GET"}


class _StubRepo:
    """Enough of the read repository to exercise the route's own logic."""

    def __init__(self, card: dict | None) -> None:
        self._card = card
        self.asked_for: str | None = None

    def case_card(self, opportunity_id: str) -> dict | None:
        self.asked_for = opportunity_id
        return self._card


def test_a_malformed_case_id_is_a_404_and_never_reaches_a_query() -> None:
    repo = _StubRepo({"stage": "lead"})
    with pytest.raises(HTTPException) as caught:
        routes.case_card(None, repo, "not-a-uuid")  # type: ignore[arg-type]
    assert caught.value.status_code == 404
    assert repo.asked_for is None


def test_an_unknown_case_is_a_404() -> None:
    with pytest.raises(HTTPException) as caught:
        routes.case_card(None, _StubRepo(None), str(uuid.uuid4()))  # type: ignore[arg-type]
    assert caught.value.status_code == 404


def test_an_unknown_stage_is_a_422_rather_than_an_empty_page() -> None:
    with pytest.raises(HTTPException) as caught:
        routes.list_cases(None, _StubRepo(None), stage="qualifiying")  # type: ignore[arg-type]
    assert caught.value.status_code == 422


@pytest.mark.parametrize("stage", sorted(STAGE_TRANSITIONS))
def test_the_card_serves_the_command_boundarys_own_stage_table(stage: str) -> None:
    """Not a copy of the stage machine — the table itself.

    Three statements of one rule (the database trigger, the command boundary and a browser)
    is one statement too many, and the one in the browser would be the one nobody re-read.
    This asserts the route hands out the boundary's table verbatim, so the dashboard can
    read it instead of holding a third copy.
    """
    card = routes.case_card(
        None,  # type: ignore[arg-type]
        _StubRepo({"stage": stage}),  # type: ignore[arg-type]
        str(uuid.uuid4()),
    )
    machine = card["stage_machine"]
    assert machine["stage"] == stage
    assert tuple(machine["allowed_next_stages"]) == STAGE_TRANSITIONS[stage]
    assert machine["is_terminal"] == (STAGE_TRANSITIONS[stage] == ())


def test_won_is_unreachable_from_every_stage_but_negotiating() -> None:
    """The one move the dashboard shows and the boundary still refuses, stated here too."""
    reachable = {
        source for source, targets in STAGE_TRANSITIONS.items() if "won" in targets
    }
    assert reachable == {"negotiating"}


# --------------------------------------------------------- database-backed proofs (opt-in)

from v2_command_harness import (  # noqa: E402
    build_disposable_database,
    needs_db as _needs_db,
    runtime_dsn as _runtime_dsn,
)


@pytest.fixture(scope="module")
def disposable_database():
    """This module's own `origenlab_test_<hex>`, migrated and dropped around it."""
    yield from build_disposable_database()


@pytest.fixture(scope="module")
def world(disposable_database):
    """One operator, one document, two institutions and one catalogued product."""
    import psycopg

    tag = uuid.uuid4().hex[:12]
    state: dict[str, object] = {"tag": tag}
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            """
            insert into platform.operator
                (auth_user_id, email_norm, display_name, role, status)
            values (gen_random_uuid(), %s, 'Pytest Operador', 'sales', 'active')
            returning id::text
            """,
            (f"pytest-read-{tag}@example.test",),
        )
        state["operator_id"] = cur.fetchone()[0]
        cur.execute(
            """
            insert into evidence.source_record (kind, dedupe_key, payload, source_uri)
            values ('gmail_message', %s, '{"subject": "pytest"}'::jsonb, %s)
            returning id::text
            """,
            (f"pytest-case-read:{tag}", f"gmail://msg/{tag}"),
        )
        state["source_record_id"] = cur.fetchone()[0]
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
            ("vendor_id", "company", f"Fabricante Ficticio {tag}"),
        ):
            cur.execute(
                "insert into crm.organization (kind, name, confirmation) "
                "values (%s, %s, 'confirmed') returning id::text, version",
                (kind, name),
            )
            row = cur.fetchone()
            state[key], state[f"{key}_version"] = row[0], row[1]
        cur.execute(
            "insert into catalog.product (manufacturer_organization_id, model_number) "
            "values (%s, %s) returning id::text",
            (state["vendor_id"], f"CX-0-{tag}"),
        )
        state["product_id"] = cur.fetchone()[0]
    return state


def _write_repo(disposable_database: str):
    import psycopg

    from origenlab_api.v2.case_command_repository import V2CaseCommandRepository

    return V2CaseCommandRepository(psycopg.connect, _runtime_dsn(disposable_database))


def _read_repo(disposable_database: str):
    import psycopg

    from origenlab_api.v2.repository import V2Repository

    return V2Repository(psycopg.connect, _runtime_dsn(disposable_database))


def _identity(world) -> OperatorIdentity:
    return OperatorIdentity(
        operator_id=world["operator_id"],
        email_norm="pytest@example.test",
        display_name="Pytest Operador",
        role="sales",
        status="active",
    )


_KEYS = iter(range(1, 1_000_000))


def _run(repo, command_name, body, world):
    return repo.execute(
        command_name=command_name,
        operator=_identity(world),
        fields=validated_case(command_name, body),
        idempotency_key=f"pytest-read-{next(_KEYS)}",
        digest=request_digest(command_name, body),
    )


@pytest.fixture(scope="module")
def built_case(disposable_database, world):
    """A case built by the real commands: an institution, an interest, two documents.

    The second part row is opened and then changed, so the card has one current reading and
    one closed one — the shape a screen must not flatten, because `crm.opportunity_organization`
    never rewrites a part.
    """
    repo = _write_repo(disposable_database)
    opened = _run(
        repo,
        OPEN_COMMERCIAL_CASE,
        OpenCommercialCaseBody(
            title=f"Caso de lectura {world['tag']}",
            origin_source_record_id=world["source_record_id"],
            note="correo entrante",
        ),
        world,
    )
    case_id = opened["opportunity_id"]
    version = opened["opportunity_version"]

    added = _run(
        repo,
        ADD_CASE_ORGANIZATION,
        AddCaseOrganizationBody(
            opportunity_id=case_id,
            opportunity_version=version,
            organization_id=world["university_id"],
            organization_version=world["university_id_version"],
            role="mentioned",
            note="el correo la nombra",
        ),
        world,
    )
    version = added["opportunity_version"]

    changed = _run(
        repo,
        SET_CASE_ORGANIZATION_ROLE,
        SetCaseOrganizationRoleBody(
            opportunity_id=case_id,
            opportunity_version=version,
            opportunity_organization_id=added["opportunity_organization_id"],
            role="requesting_institution",
            note="es quien pide",
        ),
        world,
    )
    version = changed["opportunity_version"]

    recorded = _run(
        repo,
        RECORD_CASE_INTEREST,
        RecordCaseInterestBody(
            opportunity_id=case_id,
            opportunity_version=version,
            product_id=world["product_id"],
            quantity=2,
            quantity_unit="unidades",
            note="lo pide el correo",
        ),
        world,
    )
    version = recorded["opportunity_version"]

    linked = _run(
        repo,
        LINK_CASE_EVIDENCE,
        LinkCaseEvidenceBody(
            opportunity_id=case_id,
            opportunity_version=version,
            relation="contradicts",
            assertion_id=world["assertion_id"],
            note="nombra otra institución",
        ),
        world,
    )
    return {"opportunity_id": case_id, "version": linked["opportunity_version"]}


@_needs_db
def test_the_tests_never_run_against_a_development_database(disposable_database) -> None:
    for dsn in (disposable_database, _runtime_dsn(disposable_database)):
        database = dsn.rpartition("/")[2]
        assert database.startswith("origenlab_test_")
        assert database not in {"origenlab_dev", "origenlab_clean"}


@_needs_db
def test_the_list_names_the_institution_that_is_asking(disposable_database, world, built_case):
    page = _read_repo(disposable_database).cases(
        stage=None, open_only=False, limit=50, offset=0
    )
    row = next(r for r in page.items if r["opportunity_id"] == built_case["opportunity_id"])
    assert row["requesting_organization_id"] == world["university_id"]
    assert row["requesting_confirmation"] == "confirmed"
    assert row["owner_display_name"] == "Pytest Operador"
    assert row["origin_source_kind"] == "gmail_message"


@_needs_db
def test_the_list_counts_current_rows_rather_than_every_row(disposable_database, built_case):
    """One institution, one interest, two documents.

    The case has *two* part rows — the `mentioned` one that was closed and the
    `requesting_institution` one that replaced it — so a count that included history would
    say two institutions where a human sees one.
    """
    page = _read_repo(disposable_database).cases(
        stage=None, open_only=False, limit=50, offset=0
    )
    row = next(r for r in page.items if r["opportunity_id"] == built_case["opportunity_id"])
    assert row["organization_count"] == 1
    assert row["interest_count"] == 1
    assert row["evidence_count"] == 2


@_needs_db
def test_a_stage_filter_narrows_and_an_open_only_filter_keeps_an_open_case(
    disposable_database, built_case
):
    repo = _read_repo(disposable_database)
    assert repo.cases(stage="lead", open_only=False, limit=50, offset=0).total >= 1
    assert repo.cases(stage="won", open_only=False, limit=50, offset=0).total == 0
    assert repo.cases(stage=None, open_only=True, limit=50, offset=0).total >= 1


@_needs_db
def test_the_card_shows_the_closed_part_row_beside_the_current_one(
    disposable_database, built_case
):
    card = _read_repo(disposable_database).case_card(built_case["opportunity_id"])
    assert card is not None
    roles = {(row["role"], row["is_current"]) for row in card["organizations"]}
    assert roles == {("requesting_institution", True), ("mentioned", False)}
    # Current first, so a screen that renders them in order reads correctly without sorting.
    assert card["organizations"][0]["is_current"] is True


@_needs_db
def test_the_card_reads_an_interest_through_its_catalogue_product(
    disposable_database, world, built_case
):
    card = _read_repo(disposable_database).case_card(built_case["opportunity_id"])
    assert card is not None
    interest = card["interests"][0]
    assert interest["product_id"] == world["product_id"]
    assert interest["product_name"] == f"CX-0-{world['tag']}"
    assert interest["quantity_unit"] == "unidades"
    # No price, no amount, no currency: money lives on the quote, and a read that invented a
    # column here would be the first place it leaked.
    assert not {"unit_price", "currency", "amount"} & set(interest)


@_needs_db
def test_the_card_flattens_the_typed_evidence_subject_without_losing_which_one(
    disposable_database, world, built_case
):
    card = _read_repo(disposable_database).case_card(built_case["opportunity_id"])
    assert card is not None
    by_relation = {row["relation"]: row for row in card["evidence"]}
    assert by_relation["origin"]["subject_kind"] == "source_record"
    assert by_relation["origin"]["subject_id"] == world["source_record_id"]
    assert by_relation["contradicts"]["subject_kind"] == "assertion"
    assert by_relation["contradicts"]["subject_id"] == world["assertion_id"]
    # An assertion link still reports the record it came from, so a card can show provenance
    # without the caller joining evidence.* itself.
    assert by_relation["contradicts"]["source_kind"] == "gmail_message"
    assert by_relation["contradicts"]["assertion_kind"] == "organization_name"


@_needs_db
def test_the_card_counts_history_and_current_state_separately(
    disposable_database, built_case
):
    card = _read_repo(disposable_database).case_card(built_case["opportunity_id"])
    assert card is not None
    assert card["counts"]["organizations_current"] == 1
    assert card["counts"]["organizations_total"] == 2
    assert card["counts"]["interests_open"] == 1
    assert card["counts"]["evidence_linked"] == 2


@_needs_db
def test_an_unknown_case_reads_as_none(disposable_database) -> None:
    assert _read_repo(disposable_database).case_card(str(uuid.uuid4())) is None


@_needs_db
def test_the_read_boundary_cannot_write_a_case(disposable_database, built_case) -> None:
    """The property the whole module rests on, proven by the server rather than assumed."""
    import psycopg

    repo = _read_repo(disposable_database)
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        with repo._read() as cur:  # noqa: SLF001 - the point of the test
            cur.execute(
                "update crm.opportunity set title = 'no' where id = %s",
                (built_case["opportunity_id"],),
            )
