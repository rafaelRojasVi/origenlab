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
