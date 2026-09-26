"""Role-based redaction of contact addresses on the V2 read boundary.

A `viewer` may read the dashboard, but never a contact address: every email address and every
phone channel in a `/v2` JSON answer is masked before it leaves the API, whatever field it sits
in. `sales` and `admin` read the addresses as recorded. The masking is a property of the route
class every `/v2` GET router is built with, so a new read route cannot forget it.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.contact_redaction import (
    REDACTION_HEADER,
    ContactRedactingRoute,
    redact_contact_addresses,
    sees_contact_addresses,
)
from origenlab_api.v2.identity import IdentityPort, IdentityRefused, OperatorIdentity

# Every address below is invented; the repository is public.


# ------------------------------------------------------------------ the policy


def test_only_sales_and_admin_see_contact_addresses() -> None:
    assert sees_contact_addresses("sales")
    assert sees_contact_addresses("admin")
    assert not sees_contact_addresses("viewer")
    # An unknown or empty role is refused upstream; here it fails closed all the same.
    assert not sees_contact_addresses("owner")
    assert not sees_contact_addresses("")


# ------------------------------------------------------------------ the pure walk


def test_an_email_address_is_masked_wherever_it_appears() -> None:
    payload = {
        "address": "persona@ejemplo.invalid",
        "address_norm": "persona@ejemplo.invalid",
        "sender": "Persona Ejemplo <Persona.Ejemplo@Ejemplo.invalid>",
        "recipients": "a@x.invalid, B <b@y.invalid>; c@z.invalid",
        "nested": {"items": [{"value_norm": "d+tag@w.invalid"}, "plain e@v.invalid text"]},
    }
    out = redact_contact_addresses(payload)
    assert out == {
        "address": "***@ejemplo.invalid",
        "address_norm": "***@ejemplo.invalid",
        "sender": "Persona Ejemplo <***@Ejemplo.invalid>",
        "recipients": "***@x.invalid, B <***@y.invalid>; ***@z.invalid",
        "nested": {"items": [{"value_norm": "***@w.invalid"}, "plain ***@v.invalid text"]},
    }


def test_an_address_at_a_bare_host_is_masked_too() -> None:
    # A local mailbox, an intranet host and a `Name <addr>` display string with no dotted TLD
    # are still contact addresses; the first mask only knew `user@host.tld`.
    payload = {
        "address": "user@host",
        "sender": "Root <root@localhost>",
        "recipients": "a@intranet, b@srv-01; c@host.local",
        "note": "escribir a compras@almacen mañana",
    }
    assert redact_contact_addresses(payload) == {
        "address": "***@host",
        "sender": "Root <***@localhost>",
        "recipients": "***@intranet, ***@srv-01; ***@host.local",
        "note": "escribir a ***@almacen mañana",
    }


def test_a_bare_handle_is_not_an_address() -> None:
    # `@origenlab` has no local part: a social handle or a mention, not a mailbox.
    payload = {"note": "ver @origenlab en redes", "sql": "select * from t where a @> b"}
    assert redact_contact_addresses(payload) == payload


def test_non_address_values_pass_through_untouched() -> None:
    payload = {
        "domain": "ejemplo.invalid",
        "trade_name": "Ejemplo S.A.",
        "count": 3,
        "ratio": 0.5,
        "flag": True,
        "nothing": None,
        "ids": ["11111111-1111-4111-8111-111111111111"],
        "note": "sin arroba aquí",
    }
    assert redact_contact_addresses(payload) == payload


def test_a_phone_channel_is_masked_by_its_kind() -> None:
    payload = {
        "channels": [
            {"kind": "phone", "address": "+56 9 1234 5678", "value_norm": "+56912345678"},
            {"kind": "email", "address": "p@e.invalid"},
            {"kind": "document_reference", "value_norm": "CN-00001.pdf"},
        ]
    }
    assert redact_contact_addresses(payload) == {
        "channels": [
            {"kind": "phone", "address": "***", "value_norm": "***"},
            {"kind": "email", "address": "***@e.invalid"},
            {"kind": "document_reference", "value_norm": "CN-00001.pdf"},
        ]
    }


def test_phone_named_fields_are_masked() -> None:
    assert redact_contact_addresses({"phone": "+56912345678", "phone_norm": "56912345678"}) == {
        "phone": "***",
        "phone_norm": "***",
    }


def test_the_input_is_not_mutated() -> None:
    payload = {"address": "p@e.invalid", "list": [{"sender": "q@f.invalid"}]}
    redact_contact_addresses(payload)
    assert payload == {"address": "p@e.invalid", "list": [{"sender": "q@f.invalid"}]}


# ------------------------------------------------------------------ the route class


class _Port(IdentityPort):
    def __init__(self, operator: OperatorIdentity | None) -> None:
        self._operator = operator

    def resolve(self, headers: dict[str, str]) -> OperatorIdentity:
        if self._operator is None:
            raise IdentityRefused("no identity")
        return self._operator


def _operator(role: str) -> OperatorIdentity:
    return OperatorIdentity(
        operator_id="00000000-0000-4000-8000-000000000001",
        email_norm="operator@example.invalid",
        display_name="Operator",
        role=role,
        status="active",
    )


SAMPLE = {
    "items": [
        {"contact_point_id": "c1", "address": "persona@ejemplo.invalid", "kind": "email"},
        {"contact_point_id": "c2", "address": "+56 9 1234 5678", "kind": "phone"},
    ],
    "sender": "Persona <persona@ejemplo.invalid>",
    "total": 2,
}


class _Repo:
    def contacts(self, **kwargs: Any) -> Any:
        from origenlab_api.v2.repository import Page

        return Page(items=SAMPLE["items"], total=2, limit=kwargs["limit"], offset=kwargs["offset"])

    def pipeline(self) -> dict[str, Any]:
        return SAMPLE


def _app(role: str | None) -> TestClient:
    """The real `/v2` routers over stub repositories: no database, real route classes."""
    from origenlab_api.v2.cockpit_routes import cockpit_router
    from origenlab_api.v2.crm_workspace_routes import workspace_router
    from origenlab_api.v2.routes import router as v2_router

    app = FastAPI()
    app.state.v2_identity = _Port(_operator(role) if role else None)
    app.state.v2_repository = _Repo()
    app.state.crm_workspace = _Repo()
    app.state.cockpit_repository = _Repo()
    app.include_router(v2_router)
    app.include_router(cockpit_router)
    app.include_router(workspace_router)
    return TestClient(app)


@pytest.mark.parametrize("path", ["/v2/workspace/pipeline"])
def test_a_viewer_reads_the_same_shape_without_the_addresses(path: str) -> None:
    response = _app("viewer").get(path)
    assert response.status_code == 200
    assert response.headers[REDACTION_HEADER] == "contact-addresses"
    body = response.json()
    assert body["total"] == 2
    assert body["items"][0]["address"] == "***@ejemplo.invalid"
    assert body["items"][1]["address"] == "***"
    assert body["sender"] == "Persona <***@ejemplo.invalid>"
    assert "persona@" not in response.text


@pytest.mark.parametrize("role", ["sales", "admin"])
def test_sales_and_admin_read_the_addresses_as_recorded(role: str) -> None:
    response = _app(role).get("/v2/workspace/pipeline")
    assert response.status_code == 200
    assert REDACTION_HEADER not in response.headers
    assert response.json() == SAMPLE


def test_the_contact_list_is_redacted_for_a_viewer() -> None:
    response = _app("viewer").get("/v2/contacts")
    assert response.status_code == 200
    assert "persona@" not in response.text
    assert response.headers[REDACTION_HEADER] == "contact-addresses"


def test_an_unresolved_caller_is_still_401_not_a_redacted_200() -> None:
    response = _app(None).get("/v2/workspace/pipeline")
    assert response.status_code == 401
    assert REDACTION_HEADER not in response.headers


def test_a_route_that_recorded_no_operator_is_redacted_all_the_same() -> None:
    """The route class fails closed: no recorded operator means the least-privileged view."""
    from fastapi import APIRouter

    router = APIRouter(route_class=ContactRedactingRoute)

    @router.get("/leak")
    def leak() -> dict[str, str]:
        return {"address": "persona@ejemplo.invalid"}

    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).get("/leak")
    assert response.status_code == 200
    assert response.json() == {"address": "***@ejemplo.invalid"}
    assert response.headers[REDACTION_HEADER] == "contact-addresses"


def test_every_v2_read_router_is_built_on_the_redacting_route_class() -> None:
    """A router added under `/v2` without the route class is a leak waiting to happen."""
    from origenlab_api.v2.cockpit_routes import cockpit_router
    from origenlab_api.v2.crm_workspace_routes import workspace_router
    from origenlab_api.v2.quote_case_workspace_routes import case_archive_router
    from origenlab_api.v2.quote_import_review_routes import import_review_router
    from origenlab_api.v2.routes import router as v2_router

    for router in (v2_router, cockpit_router, workspace_router, case_archive_router, import_review_router):
        assert router.route_class is ContactRedactingRoute, router.prefix
        for route in router.routes:
            assert isinstance(route, ContactRedactingRoute), getattr(route, "path", route)


def test_every_v2_read_router_is_get_only() -> None:
    from origenlab_api.v2.cockpit_routes import cockpit_router
    from origenlab_api.v2.crm_workspace_routes import workspace_router
    from origenlab_api.v2.quote_case_workspace_routes import case_archive_router
    from origenlab_api.v2.quote_import_review_routes import import_review_router
    from origenlab_api.v2.routes import router as v2_router

    for router in (v2_router, cockpit_router, workspace_router, case_archive_router, import_review_router):
        for route in router.routes:
            methods = set(getattr(route, "methods", set()))
            assert methods <= {"GET", "HEAD", "OPTIONS"}, f"{route.path}: {methods}"


def _flatten(routes: Any) -> Any:
    """Every concrete route, through the included-router wrappers FastAPI mounts."""
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _flatten(inner.routes)
        elif hasattr(route, "routes") and not hasattr(route, "endpoint"):
            yield from _flatten(route.routes)
        else:
            yield route


def test_the_app_mounts_every_v2_get_route_on_the_redacting_route_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whatever `create_app` mounts under `/v2` with GET redacts for a viewer."""
    import secrets

    from origenlab_api.main import create_app

    monkeypatch.setenv("ORIGENLAB_DISABLE_DOTENV", "1")
    monkeypatch.setenv("ORIGENLAB_V2_DATABASE_URL", "postgresql://u:p@127.0.0.1:54332/unused")
    monkeypatch.delenv("ORIGENLAB_V2_JWKS_URL", raising=False)
    monkeypatch.setenv("ORIGENLAB_GOOGLE_AUTH_ENABLED", "false")
    monkeypatch.setenv("ORIGENLAB_DEV_LOGIN_ENABLED", "true")
    monkeypatch.setenv("ORIGENLAB_AUTH_SESSION_SECRET", secrets.token_urlsafe(48))
    monkeypatch.delenv("ORIGENLAB_ENV", raising=False)
    app = create_app()
    v2_get_routes = [
        route
        for route in _flatten(app.routes)
        if getattr(route, "path", "").startswith("/v2/") and "GET" in getattr(route, "methods", set())
    ]
    assert v2_get_routes, "no /v2 GET route mounted"
    for route in v2_get_routes:
        assert isinstance(route, ContactRedactingRoute), route.path


def test_the_dev_header_viewer_is_redacted_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """Through the real identity port: the dev header names a viewer, the answer is masked."""
    import secrets

    from origenlab_api.main import create_app
    from origenlab_api.v2.identity import OperatorLookup

    class _Lookup(OperatorLookup):
        def by_email(self, email_norm: str) -> OperatorIdentity | None:
            return _operator("viewer")

    monkeypatch.setenv("ORIGENLAB_DISABLE_DOTENV", "1")
    monkeypatch.setenv("ORIGENLAB_V2_DATABASE_URL", "postgresql://u:p@127.0.0.1:54332/unused")
    monkeypatch.delenv("ORIGENLAB_V2_JWKS_URL", raising=False)
    monkeypatch.setenv("ORIGENLAB_GOOGLE_AUTH_ENABLED", "false")
    monkeypatch.setenv("ORIGENLAB_DEV_LOGIN_ENABLED", "true")
    monkeypatch.setenv("ORIGENLAB_AUTH_SESSION_SECRET", secrets.token_urlsafe(48))
    monkeypatch.delenv("ORIGENLAB_ENV", raising=False)
    app = create_app()
    from origenlab_api.v2.identity import build_identity_port

    app.state.v2_identity = build_identity_port(
        jwks_url=None,
        database_url="postgresql://u:p@127.0.0.1:54332/unused",
        lookup=_Lookup(),
        dev_login_enabled=True,
    )
    app.state.crm_workspace = _Repo()
    client = TestClient(app)
    response = client.get("/v2/workspace/pipeline", headers={OPERATOR_EMAIL_HEADER: "viewer@example.invalid"})
    assert response.status_code == 200
    assert "persona@" not in response.text
    assert response.headers[REDACTION_HEADER] == "contact-addresses"


# ------------------------------------------------------------------ the PDF route


def _import_review_app(role: str) -> TestClient:
    from origenlab_api.v2.quote_import_review_routes import import_review_router

    app = FastAPI()
    app.state.v2_identity = _Port(_operator(role))
    # No `import_review` state on purpose: the role is checked before the state is needed.
    app.include_router(import_review_router)
    return TestClient(app)


def test_a_viewer_may_not_open_a_quotation_pdf() -> None:
    """A file cannot be masked, so an operator without the role is refused outright."""
    response = _import_review_app("viewer").get(f"/v2/cockpit/import-review/documents/{'a' * 64}")
    assert response.status_code == 403


def test_the_pdf_role_check_comes_before_anything_else() -> None:
    response = _import_review_app("sales").get(f"/v2/cockpit/import-review/documents/{'a' * 64}")
    assert response.status_code == 503  # past the role check; the review is not configured here


# ------------------------------------------------------------------ the search oracle
#
# Masking the answer is not enough when the *question* can name an address: a viewer who
# may not read `persona@ejemplo.invalid` must not learn it exists by typing `persona@` into
# the search box and getting a masked hit back. For a viewer, `q` on `/v2/contacts` reaches
# only the recorded person's name and the recorded institution's name.


class _RecordingRepo(_Repo):
    def __init__(self) -> None:
        self.contact_calls: list[dict[str, Any]] = []

    def contacts(self, **kwargs: Any) -> Any:
        self.contact_calls.append(kwargs)
        return super().contacts(**kwargs)


def _searching_app(role: str) -> tuple[TestClient, _RecordingRepo]:
    from origenlab_api.v2.routes import router as v2_router

    repo = _RecordingRepo()
    app = FastAPI()
    app.state.v2_identity = _Port(_operator(role))
    app.state.v2_repository = repo
    app.include_router(v2_router)
    return TestClient(app), repo


def test_a_viewer_search_is_scoped_to_names_only() -> None:
    client, repo = _searching_app("viewer")
    response = client.get("/v2/contacts", params={"q": "persona@ejemplo"})
    assert response.status_code == 200
    assert response.headers[REDACTION_HEADER] == "contact-addresses"
    assert repo.contact_calls == [
        {
            "q": "persona@ejemplo",
            "identity": None,
            "with_cases": False,
            "search_addresses": False,
            "limit": repo.contact_calls[0]["limit"],
            "offset": 0,
        }
    ]


@pytest.mark.parametrize("role", ["sales", "admin"])
def test_sales_and_admin_still_search_addresses(role: str) -> None:
    client, repo = _searching_app(role)
    response = client.get("/v2/contacts", params={"q": "persona@ejemplo"})
    assert response.status_code == 200
    assert REDACTION_HEADER not in response.headers
    assert repo.contact_calls[0]["search_addresses"] is True


class _RecordingCursor:
    """A cursor that records the SQL it is handed and answers an empty page."""

    def __init__(self, log: list[tuple[str, Any]]) -> None:
        self._log = log
        self.description = [("contact_point_id",)]

    def execute(self, sql: str, params: Any = None) -> None:
        self._log.append((sql, params))

    def fetchone(self) -> tuple[int]:
        return (0,)

    def fetchall(self) -> list[Any]:
        return []

    def __enter__(self) -> "_RecordingCursor":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class _RecordingConnection:
    def __init__(self, log: list[tuple[str, Any]]) -> None:
        self._log = log

    def cursor(self) -> _RecordingCursor:
        return _RecordingCursor(self._log)

    def rollback(self) -> None:
        return None

    def __enter__(self) -> "_RecordingConnection":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


def _recording_repository() -> tuple[Any, list[tuple[str, Any]]]:
    from origenlab_api.v2.repository import V2Repository

    log: list[tuple[str, Any]] = []
    repo = V2Repository(lambda dsn, autocommit: _RecordingConnection(log), "postgresql://unused")
    return repo, log


def _search_statements(log: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
    return [(sql, params) for sql, params in log if "from crm.contact_point cp" in sql]


def test_the_repository_never_compares_an_address_for_a_name_only_search() -> None:
    repo, log = _recording_repository()
    repo.contacts(q="persona@ejemplo", limit=10, offset=0, search_addresses=False)
    statements = _search_statements(log)
    assert len(statements) == 2  # the count and the page
    for sql, params in statements:
        where = sql.split("where", 1)[1]
        assert "value_norm like" not in where, where
        assert "lower(p.display_name) like %s" in where
        assert "lower(o.name) like %s" in where
        assert list(params[:2]) == ["%persona@ejemplo%", "%persona@ejemplo%"]
        # Exactly the two name needles: nothing is left for an address predicate to bind.
        assert list(params).count("%persona@ejemplo%") == 2


def test_the_repository_still_compares_the_address_when_asked() -> None:
    repo, log = _recording_repository()
    repo.contacts(q="persona@ejemplo", limit=10, offset=0, search_addresses=True)
    for sql, params in _search_statements(log):
        where = sql.split("where", 1)[1]
        assert "cp.value_norm like %s" in where
        assert list(params).count("%persona@ejemplo%") == 3


def test_the_repository_search_fails_closed_when_the_scope_is_not_named() -> None:
    """A caller that forgot to say who is asking gets the viewer's search, not the admin's."""
    repo, log = _recording_repository()
    repo.contacts(q="persona@ejemplo", limit=10, offset=0)
    for sql, _params in _search_statements(log):
        assert "value_norm like" not in sql.split("where", 1)[1]


# ------------------------------------------------------ the evidence search oracle
#
# `/v2/evidence?q=` has the same shape of leak with a different table: `evidence.assertion`
# holds observed addresses (`contact_address`, `contacted_address`, `postal_address`) next to
# observed names, and a substring search over every `value_norm` lets a viewer confirm a
# masked address exists. For a viewer the search reaches only the non-address kinds, and
# never a value that carries an `@`; `sales` and `admin` keep the whole trail searchable.


class _EvidenceRecordingRepo(_Repo):
    def __init__(self) -> None:
        self.evidence_calls: list[dict[str, Any]] = []

    def evidence(self, **kwargs: Any) -> Any:
        from origenlab_api.v2.repository import Page

        self.evidence_calls.append(kwargs)
        return Page(items=[], total=0, limit=kwargs["limit"], offset=kwargs["offset"])


def _evidence_app(role: str) -> tuple[TestClient, _EvidenceRecordingRepo]:
    from origenlab_api.v2.routes import router as v2_router

    repo = _EvidenceRecordingRepo()
    app = FastAPI()
    app.state.v2_identity = _Port(_operator(role))
    app.state.v2_repository = repo
    app.include_router(v2_router)
    return TestClient(app), repo


def test_a_viewer_evidence_search_does_not_reach_addresses() -> None:
    client, repo = _evidence_app("viewer")
    response = client.get("/v2/evidence", params={"q": "persona@ejemplo"})
    assert response.status_code == 200
    assert response.headers[REDACTION_HEADER] == "contact-addresses"
    assert repo.evidence_calls == [
        {
            "q": "persona@ejemplo",
            "resolution": None,
            "source_kind": None,
            "search_addresses": False,
            "limit": repo.evidence_calls[0]["limit"],
            "offset": 0,
        }
    ]


@pytest.mark.parametrize("role", ["sales", "admin"])
def test_sales_and_admin_still_search_the_whole_evidence_trail(role: str) -> None:
    client, repo = _evidence_app(role)
    response = client.get("/v2/evidence", params={"q": "persona@ejemplo"})
    assert response.status_code == 200
    assert REDACTION_HEADER not in response.headers
    assert repo.evidence_calls[0]["search_addresses"] is True


def _evidence_statements(log: list[tuple[str, Any]]) -> list[tuple[str, Any]]:
    return [(sql, params) for sql, params in log if "from evidence.assertion a" in sql]


def test_the_repository_keeps_address_kinds_out_of_a_scoped_evidence_search() -> None:
    from origenlab_api.v2.repository import V2Repository

    repo, log = _recording_repository()
    repo.evidence(
        q="persona@ejemplo", resolution=None, source_kind=None, limit=10, offset=0,
        search_addresses=False,
    )
    statements = _evidence_statements(log)
    assert len(statements) == 2  # the count and the page
    for sql, params in statements:
        where = sql.split("where", 1)[1]
        assert "a.value_norm like %s" in where
        assert "a.kind <> all(%s)" in where
        assert "position('@' in a.value_norm) = 0" in where
        assert list(params[:2]) == [
            list(V2Repository.EVIDENCE_ADDRESS_KINDS),
            "%persona@ejemplo%",
        ]
    assert set(V2Repository.EVIDENCE_ADDRESS_KINDS) == {
        "contact_address", "contacted_address", "postal_address",
    }


def test_the_repository_searches_every_evidence_kind_when_asked() -> None:
    repo, log = _recording_repository()
    repo.evidence(
        q="persona@ejemplo", resolution=None, source_kind=None, limit=10, offset=0,
        search_addresses=True,
    )
    for sql, params in _evidence_statements(log):
        where = sql.split("where", 1)[1]
        assert "a.value_norm like %s" in where
        assert "a.kind <> all" not in where
        assert "position('@'" not in where
        assert list(params[:1]) == ["%persona@ejemplo%"]


def test_the_evidence_search_fails_closed_when_the_scope_is_not_named() -> None:
    repo, log = _recording_repository()
    repo.evidence(q="persona@ejemplo", resolution=None, source_kind=None, limit=10, offset=0)
    for sql, _params in _evidence_statements(log):
        assert "a.kind <> all(%s)" in sql.split("where", 1)[1]


def test_an_evidence_listing_without_a_query_has_no_kind_filter() -> None:
    """The scope narrows the *search*, not the listing: a viewer still sees every masked row."""
    repo, log = _recording_repository()
    repo.evidence(q=None, resolution=None, source_kind=None, limit=10, offset=0)
    for sql, _params in _evidence_statements(log):
        assert "a.kind <> all" not in sql


# ------------------------------------------ the evidence search oracle, on real rows
#
# Proven in a disposable `origenlab_test_<hex>` database the harness creates and drops, so
# the clean room and `origenlab_dev` are unreachable by construction. Every value is
# invented. Skips without `ORIGENLAB_V2_TEST_DSN`.

import os as _os  # noqa: E402

from v2_command_harness import build_disposable_database  # noqa: E402

_needs_db = pytest.mark.skipif(
    not _os.environ.get("ORIGENLAB_V2_TEST_DSN", "").strip(),
    reason="ORIGENLAB_V2_TEST_DSN is not set",
)

#: (kind, value_norm) — every row carries the needle `persona`, so the only thing that
#: separates a viewer's hits from an admin's is the kind and the `@`.
_SEEDED_ASSERTIONS: tuple[tuple[str, str], ...] = (
    ("contact_address", "persona@ejemplo.invalid"),
    ("contacted_address", "persona.dos@ejemplo.invalid"),
    ("postal_address", "calle persona 123, ciudad ficticia"),
    ("organization_name", "instituto persona ficticio"),
    ("document_reference", "persona-cn-00001.pdf"),
    # A name-kind row whose value is address-shaped: the `@` guard is what keeps it out.
    ("organization_name", "persona@buzon.invalid"),
)


@pytest.fixture(scope="module")
def evidence_database():
    """This module's own disposable database, seeded with one record and six assertions."""
    import psycopg

    for dsn in build_disposable_database():
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("set role origenlab_owner")
            cur.execute(
                """
                insert into evidence.source_record (kind, dedupe_key, payload, source_uri)
                values ('gmail_message', 'pytest-evidence-oracle', '{"subject": "pytest"}'::jsonb,
                        'gmail://msg/pytest-evidence-oracle')
                returning id::text
                """
            )
            source_record_id = cur.fetchone()[0]
            for kind, value_norm in _SEEDED_ASSERTIONS:
                cur.execute(
                    "insert into evidence.assertion (source_record_id, kind, value_norm) "
                    "values (%s, %s, %s)",
                    (source_record_id, kind, value_norm),
                )
        yield dsn


def _database_repository(dsn: str) -> Any:
    import psycopg

    from origenlab_api.v2.repository import V2Repository

    return V2Repository(psycopg.connect, dsn)


@_needs_db
def test_on_real_rows_a_scoped_search_returns_only_non_address_kinds(evidence_database) -> None:
    repo = _database_repository(evidence_database)
    page = repo.evidence(
        q="persona", resolution=None, source_kind=None, limit=50, offset=0,
        search_addresses=False,
    )
    assert sorted(item["value_norm"] for item in page.items) == [
        "instituto persona ficticio",
        "persona-cn-00001.pdf",
    ]
    assert page.total == 2
    for item in page.items:
        assert item["kind"] not in repo.EVIDENCE_ADDRESS_KINDS
        assert "@" not in item["value_norm"]


@_needs_db
@pytest.mark.parametrize("needle", ["persona@", "ejemplo.invalid", "calle persona", "buzon"])
def test_on_real_rows_an_address_needle_finds_nothing_for_a_viewer(
    evidence_database, needle: str
) -> None:
    repo = _database_repository(evidence_database)
    page = repo.evidence(
        q=needle, resolution=None, source_kind=None, limit=50, offset=0, search_addresses=False
    )
    assert page.total == 0
    assert page.items == []


@_needs_db
def test_on_real_rows_an_admin_search_still_reaches_every_kind(evidence_database) -> None:
    repo = _database_repository(evidence_database)
    page = repo.evidence(
        q="persona", resolution=None, source_kind=None, limit=50, offset=0,
        search_addresses=True,
    )
    assert page.total == len(_SEEDED_ASSERTIONS)
    assert sorted(item["value_norm"] for item in page.items) == sorted(
        value for _kind, value in _SEEDED_ASSERTIONS
    )


@_needs_db
def test_on_real_rows_a_viewer_listing_is_still_the_whole_masked_trail(evidence_database) -> None:
    """Without `q` the scope does not apply: every row is listed, and every address masked."""
    from origenlab_api.v2.routes import router as v2_router

    app = FastAPI()
    app.state.v2_identity = _Port(_operator("viewer"))
    app.state.v2_repository = _database_repository(evidence_database)
    app.include_router(v2_router)
    response = TestClient(app).get("/v2/evidence", params={"limit": 50})
    assert response.status_code == 200
    assert response.headers[REDACTION_HEADER] == "contact-addresses"
    body = response.json()
    assert body["total"] == len(_SEEDED_ASSERTIONS)
    assert "persona@" not in response.text
    assert "***@ejemplo.invalid" in response.text


@_needs_db
def test_on_real_rows_the_route_never_confirms_a_masked_address_for_a_viewer(
    evidence_database,
) -> None:
    """End to end: the real route over the real repository, asked the oracle's question."""
    from origenlab_api.v2.routes import router as v2_router

    app = FastAPI()
    app.state.v2_identity = _Port(_operator("viewer"))
    app.state.v2_repository = _database_repository(evidence_database)
    app.include_router(v2_router)
    client = TestClient(app)
    hidden = client.get("/v2/evidence", params={"q": "persona@ejemplo"})
    assert hidden.status_code == 200
    assert hidden.headers[REDACTION_HEADER] == "contact-addresses"
    assert hidden.json()["total"] == 0
    visible = client.get("/v2/evidence", params={"q": "instituto persona"})
    assert visible.json()["total"] == 1
    assert visible.json()["items"][0]["kind"] == "organization_name"
