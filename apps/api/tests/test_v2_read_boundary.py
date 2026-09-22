"""Tests for the V2 durable read boundary.

The properties worth asserting here are the boundary's, not the query's: that the router is
absent when unconfigured, that the development identity adapter cannot be pointed at a
non-loopback database, that a disabled operator is refused, and that nothing in the package
can write.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from origenlab_api.main import create_app
from origenlab_api.settings import Settings
from origenlab_api.v2.identity import (
    IdentityMisconfigured,
    IdentityRefused,
    JwksVerifier,
    LocalDevIdentity,
    OperatorIdentity,
    OperatorLookup,
    build_identity_port,
    is_loopback_dsn,
)
from origenlab_api.v2.repository import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, clamp_limit

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER

LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"


class _Lookup(OperatorLookup):
    def __init__(self, operator: OperatorIdentity | None) -> None:
        self._operator = operator

    def by_email(self, email_norm: str) -> OperatorIdentity | None:
        return self._operator


def _operator(role: str = "admin", status: str = "active") -> OperatorIdentity:
    return OperatorIdentity(
        operator_id="00000000-0000-4000-8000-000000000001",
        email_norm="operator@example.cl",
        display_name="Operator",
        role=role,
        status=status,
    )


# ------------------------------------------------------------------ loopback boundary


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://u:p@127.0.0.1:54332/db",
        "postgres://u:p@127.0.0.1:5432/db",
        "postgresql://u:p@[::1]:54332/db",
    ],
)
def test_literal_loopback_addresses_are_accepted(dsn: str) -> None:
    assert is_loopback_dsn(dsn) is True


@pytest.mark.parametrize(
    "dsn",
    [
        # A host *name* is refused, `localhost` included: what it resolves to is decided
        # outside this process and is not a property of the string we were handed.
        "postgresql://u:p@localhost:54332/db",
        "postgresql://u:p@db.example.supabase.co:5432/db",
        "postgresql://u:p@10.0.0.5:5432/db",
        "postgresql://u:p@192.168.1.10:5432/db",
        "mysql://u:p@127.0.0.1:3306/db",
        "not a dsn",
        "",
    ],
)
def test_everything_else_is_refused(dsn: str) -> None:
    assert is_loopback_dsn(dsn) is False


def test_the_dev_identity_adapter_refuses_a_non_loopback_database() -> None:
    with pytest.raises(IdentityMisconfigured) as excinfo:
        LocalDevIdentity(
            "postgresql://u:p@db.example.supabase.co:5432/postgres", _Lookup(_operator())
        )
    assert "no override" in str(excinfo.value)


def test_jwks_wins_whenever_it_is_configured() -> None:
    # A deployment that has configured JWKS must never silently fall back to the header
    # adapter, even against a loopback database.
    port = build_identity_port(
        jwks_url="https://example.supabase.co/auth/v1/.well-known/jwks.json",
        database_url=LOOPBACK,
        lookup=_Lookup(_operator()),
    )
    assert isinstance(port, JwksVerifier)


def test_the_dev_adapter_is_chosen_only_when_no_jwks_url_exists() -> None:
    port = build_identity_port(jwks_url=None, database_url=LOOPBACK, lookup=_Lookup(_operator()))
    assert isinstance(port, LocalDevIdentity)


def test_jwks_without_a_url_refuses_at_construction() -> None:
    with pytest.raises(IdentityMisconfigured):
        JwksVerifier("   ", _Lookup(_operator()))


# ----------------------------------------------------------------------- identity


def test_a_missing_header_is_refused() -> None:
    port = LocalDevIdentity(LOOPBACK, _Lookup(_operator()))
    with pytest.raises(IdentityRefused):
        port.resolve({})


def test_an_unknown_operator_is_refused() -> None:
    port = LocalDevIdentity(LOOPBACK, _Lookup(None))
    with pytest.raises(IdentityRefused):
        port.resolve({LocalDevIdentity.HEADER: "nobody@example.cl"})


def test_a_disabled_operator_is_refused_even_with_a_valid_identity() -> None:
    port = LocalDevIdentity(LOOPBACK, _Lookup(_operator(status="disabled")))
    with pytest.raises(IdentityRefused) as excinfo:
        port.resolve({LocalDevIdentity.HEADER: "operator@example.cl"})
    assert "disabled" in str(excinfo.value)


def test_the_dev_adapter_reads_the_same_header_the_proxy_reconstructs() -> None:
    # The proxy deletes exactly this header and rebuilds it from Cloudflare Access. A V2
    # boundary reading any other name would receive a browser-supplied value the proxy has
    # no reason to strip, and any signed-in user could impersonate any operator.
    assert LocalDevIdentity.HEADER == OPERATOR_EMAIL_HEADER.lower()


def test_role_gating() -> None:
    assert _operator(role="viewer").require_role("viewer", "sales", "admin").role == "viewer"
    with pytest.raises(IdentityRefused):
        _operator(role="viewer").require_role("admin")


# -------------------------------------------------------------------------- paging


def test_limits_are_clamped_so_one_request_cannot_export_the_contact_database() -> None:
    assert clamp_limit(None) == DEFAULT_PAGE_SIZE
    assert clamp_limit(10_000) == MAX_PAGE_SIZE
    assert clamp_limit(0) == 1
    assert clamp_limit(-5) == 1
    assert clamp_limit(25) == 25


# --------------------------------------------------------------------- app mounting


def test_the_v2_router_is_absent_when_no_v2_database_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An unconfigured deployment gets no V2 surface at all — not one that answers 503 on
    # every request.
    monkeypatch.delenv("ORIGENLAB_V2_DATABASE_URL", raising=False)
    app = create_app()
    client = TestClient(app)
    assert client.get("/v2/organizations").status_code == 404


def test_settings_default_to_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    # Explicitly cleared: a developer with a local V2 DSN exported would otherwise see this
    # pass or fail depending on their shell, which is not a property of the code.
    monkeypatch.delenv("ORIGENLAB_V2_DATABASE_URL", raising=False)
    settings = Settings()
    assert settings.v2_configured() is False
    with pytest.raises(ValueError):
        settings.require_v2_database_url()


# ------------------------------------------------------------------ write-freeness


def test_the_v2_package_defines_no_write_route() -> None:
    from origenlab_api.v2.routes import router

    methods: set[str] = set()
    for route in router.routes:
        methods |= set(getattr(route, "methods", set()))
    # The V2 surface is a read boundary. Durable writes belong to the command boundary of
    # docs/ARCHITECTURE.md, and a write verb appearing here would be the first sign that a
    # second writer had grown.
    assert methods <= {"GET", "HEAD", "OPTIONS"}, f"unexpected write verbs: {methods}"


def test_the_repository_contains_no_mutating_sql() -> None:
    import inspect

    from origenlab_api.v2 import repository

    source = inspect.getsource(repository).lower()
    for verb in (" insert into ", " update ", " delete from ", " truncate ", " drop "):
        assert verb not in source, f"repository contains {verb.strip()!r}"


# --------------------------------------------------------- database-backed proofs
#
# Opt-in. These need a loopback PostgreSQL carrying the Slice 0 schema, named by
# ORIGENLAB_V2_TEST_DSN — the same convention the migration suite uses. Without one they
# skip, so CI stays database-free here while a developer can still prove the real thing.

import os  # noqa: E402

_TEST_DSN = os.environ.get("ORIGENLAB_V2_TEST_DSN", "").strip()
_needs_db = pytest.mark.skipif(not _TEST_DSN, reason="ORIGENLAB_V2_TEST_DSN is not set")


@_needs_db
def test_a_write_is_refused_by_the_server() -> None:
    """The read-only guarantee is the server's, not a convention this code follows.

    Asserting it here means a future refactor that loses `set transaction read only` fails
    loudly instead of quietly turning the read boundary into a second writer.
    """
    import psycopg

    from origenlab_api.v2.repository import V2Repository

    repo = V2Repository(psycopg.connect, _TEST_DSN)
    with repo._read() as cur:
        cur.execute("show transaction_read_only")
        assert cur.fetchone()[0] == "on"
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            cur.execute(
                "insert into crm.organization (kind, name, confirmation) "
                "values ('probe', 'probe', 'machine_proposed')"
            )


@_needs_db
def test_the_statement_timeout_is_applied_inside_the_transaction() -> None:
    # `set local` outside a transaction is silently a no-op, so this asserts the value the
    # server actually holds rather than the one that was requested.
    import psycopg

    from origenlab_api.v2.repository import V2Repository

    repo = V2Repository(psycopg.connect, _TEST_DSN, statement_timeout_ms=4321)
    with repo._read() as cur:
        cur.execute("show statement_timeout")
        assert cur.fetchone()[0] == "4321ms"


# ------------------------------------------------------- cards and evidence, in process
#
# These mount the router over a stub repository. The point is the boundary's behaviour —
# what a malformed identifier does, what a value the schema cannot hold does, and that a
# missing row is a 404 rather than an empty card — none of which needs a database.


class _StubRepo:
    EVIDENCE_RESOLUTIONS = ("unresolved", "promoted", "linked", "rejected", "ambiguous")
    EVIDENCE_SOURCE_KINDS = (
        "workbook_import", "chilecompra_notice", "migration_manifest",
        "v1_parse_failure", "v1_evidence_edge", "v1_supplier_candidate",
        "v1_historical_quote_candidate", "gmail_message", "drive_file",
    )

    def __init__(self, *, contact=None, organization=None) -> None:
        self.contact = contact
        self.organization = organization
        self.evidence_calls: list[dict] = []

    def contact_card(self, contact_point_id: str):
        return self.contact

    def organization_card(self, organization_id: str):
        return self.organization

    def evidence(self, **kwargs):
        from origenlab_api.v2.repository import Page

        self.evidence_calls.append(kwargs)
        return Page(items=[], total=0, limit=kwargs["limit"], offset=kwargs["offset"])


def _client(repo: _StubRepo) -> TestClient:
    from fastapi import FastAPI

    from origenlab_api.v2.routes import router

    app = FastAPI()
    app.include_router(router)
    app.state.v2_repository = repo
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(_operator()))
    client = TestClient(app)
    client.headers.update({OPERATOR_EMAIL_HEADER: "operator@example.cl"})
    return client


def test_a_contact_card_identifier_that_is_not_a_uuid_is_a_404_not_a_500() -> None:
    # Without the boundary guard this reaches the driver as invalid input and surfaces as a
    # 500. A path segment that is not a UUID names nothing.
    response = _client(_StubRepo()).get("/v2/contacts/not-a-uuid")
    assert response.status_code == 404


def test_a_contact_that_does_not_exist_is_a_404_not_an_empty_card() -> None:
    response = _client(_StubRepo(contact=None)).get(
        "/v2/contacts/00000000-0000-4000-8000-000000000001"
    )
    assert response.status_code == 404


def test_a_contact_card_is_returned_whole() -> None:
    card = {
        "contact_point_id": "00000000-0000-4000-8000-000000000001",
        "address": "compras@uni.example",
        "usage": "shared_mailbox",
        "person_id": None,
        "organization_id": None,
        "sibling_contact_points": [],
        "affiliations": [],
        "evidence": [{"assertion_id": "a", "source_kind": "migration_manifest"}],
        "marketing": [],
        "address_controls": [],
        "counts": {"evidence": 1},
    }
    response = _client(_StubRepo(contact=card)).get(
        "/v2/contacts/00000000-0000-4000-8000-000000000001"
    )
    assert response.status_code == 200
    assert response.json() == card


def test_an_organization_card_that_does_not_exist_is_a_404() -> None:
    response = _client(_StubRepo(organization=None)).get(
        "/v2/organizations/00000000-0000-4000-8000-000000000002"
    )
    assert response.status_code == 404


def test_an_organization_identifier_that_is_not_a_uuid_is_a_404() -> None:
    assert _client(_StubRepo()).get("/v2/organizations/12345").status_code == 404


def test_the_card_routes_require_an_operator() -> None:
    from fastapi import FastAPI

    from origenlab_api.v2.routes import router

    app = FastAPI()
    app.include_router(router)
    app.state.v2_repository = _StubRepo(contact={"contact_point_id": "x"})
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(_operator()))
    client = TestClient(app)  # no operator header
    assert client.get("/v2/contacts/00000000-0000-4000-8000-000000000001").status_code == 401
    assert client.get("/v2/organizations/00000000-0000-4000-8000-000000000001").status_code == 401
    assert client.get("/v2/evidence").status_code == 401


def test_an_evidence_resolution_the_schema_cannot_hold_is_a_422() -> None:
    # An unchecked filter would return an empty page, which reads as "there is nothing" —
    # the one answer a review queue must never give wrongly.
    response = _client(_StubRepo()).get("/v2/evidence", params={"resolution": "maybe"})
    assert response.status_code == 422


def test_an_evidence_source_kind_the_schema_cannot_hold_is_a_422() -> None:
    response = _client(_StubRepo()).get("/v2/evidence", params={"source_kind": "scraped"})
    assert response.status_code == 422


def test_the_staged_gmail_and_drive_kinds_are_accepted_filters() -> None:
    repo = _StubRepo()
    client = _client(repo)
    for kind in ("gmail_message", "drive_file"):
        assert client.get("/v2/evidence", params={"source_kind": kind}).status_code == 200
    assert [call["source_kind"] for call in repo.evidence_calls] == [
        "gmail_message",
        "drive_file",
    ]


def test_the_evidence_listing_is_bounded_like_every_other_listing() -> None:
    repo = _StubRepo()
    assert _client(repo).get("/v2/evidence", params={"limit": 500}).status_code == 422
    body = _client(repo).get("/v2/evidence", params={"limit": 200}).json()
    assert body["limit"] == 200


# ------------------------------------------- database-backed proofs of the new reads


@_needs_db
def test_a_contact_card_reads_the_real_channel_and_its_provenance() -> None:
    import psycopg

    from origenlab_api.v2.repository import V2Repository

    repo = V2Repository(psycopg.connect, _TEST_DSN)
    first = repo.contacts(q=None, limit=1, offset=0)
    if first.total == 0:
        pytest.skip("the target database holds no contact points")
    card = repo.contact_card(first.items[0]["contact_point_id"])
    assert card is not None
    assert card["contact_point_id"] == first.items[0]["contact_point_id"]
    # Every list on the card is present even when empty: an absent key and an empty list
    # mean different things to the UI, and only one of them is honest here.
    for key in (
        "sibling_contact_points",
        "affiliations",
        "evidence",
        "marketing",
        "address_controls",
    ):
        assert isinstance(card[key], list)
        assert len(card[key]) <= V2Repository.CARD_CHILD_LIMIT
        assert card["counts"][key] >= len(card[key])


@_needs_db
def test_an_organization_card_reads_its_channels_and_counts_them_truthfully() -> None:
    import psycopg

    from origenlab_api.v2.repository import V2Repository

    repo = V2Repository(psycopg.connect, _TEST_DSN)
    first = repo.organizations(q=None, limit=1, offset=0)
    if first.total == 0:
        pytest.skip("the target database holds no organizations")
    card = repo.organization_card(first.items[0]["organization_id"])
    assert card is not None
    assert card["organization_id"] == first.items[0]["organization_id"]
    for key in (
        "contact_points",
        "people",
        "domains",
        "relationships",
        "child_organizations",
        "evidence",
    ):
        assert isinstance(card[key], list)
        assert len(card[key]) <= V2Repository.CARD_CHILD_LIMIT
        assert card["counts"][key] >= len(card[key])


@_needs_db
def test_a_card_for_an_identifier_that_exists_nowhere_is_none() -> None:
    import psycopg

    from origenlab_api.v2.repository import V2Repository

    repo = V2Repository(psycopg.connect, _TEST_DSN)
    absent = "00000000-0000-4000-8000-0000deadbeef"
    assert repo.contact_card(absent) is None
    assert repo.organization_card(absent) is None


@_needs_db
def test_the_evidence_listing_agrees_with_the_review_summary() -> None:
    """The list and the counter must not disagree about the same population.

    `/v2/review/summary` is what the dashboard card shows; `/v2/evidence` is what an
    operator opens to act on it. A discrepancy between them is the failure that makes a
    review queue untrustworthy.
    """
    import psycopg

    from origenlab_api.v2.repository import V2Repository

    repo = V2Repository(psycopg.connect, _TEST_DSN)
    summary = repo.review_summary()
    for resolution, key in (
        ("ambiguous", "ambiguous_assertions"),
        ("unresolved", "unresolved_assertions"),
    ):
        page = repo.evidence(
            q=None, resolution=resolution, source_kind=None, limit=1, offset=0
        )
        assert page.total == summary[key], resolution


@_needs_db
def test_every_evidence_row_carries_the_record_it_came_from() -> None:
    import psycopg

    from origenlab_api.v2.repository import V2Repository

    repo = V2Repository(psycopg.connect, _TEST_DSN)
    page = repo.evidence(q=None, resolution=None, source_kind=None, limit=50, offset=0)
    if page.total == 0:
        pytest.skip("the target database holds no assertions")
    for row in page.items:
        assert row["source_record_id"]
        assert row["source_kind"] in V2Repository.EVIDENCE_SOURCE_KINDS
        assert row["resolution"] in V2Repository.EVIDENCE_RESOLUTIONS


@_needs_db
def test_the_closed_source_kind_list_matches_the_database() -> None:
    """The filter vocabulary and the CHECK constraint must not drift apart.

    If a migration adds a kind and this list is not updated, that kind becomes unfilterable
    and silently invisible in the review queue.
    """
    import psycopg
    import re

    from origenlab_api.v2.repository import V2Repository

    with psycopg.connect(_TEST_DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "select pg_get_constraintdef(oid) from pg_constraint "
            "where conname = 'source_record_kind_check'"
        )
        definition = cur.fetchone()[0]
    in_database = set(re.findall(r"'([a-z0-9_]+)'::text", definition))
    assert in_database == set(V2Repository.EVIDENCE_SOURCE_KINDS)
