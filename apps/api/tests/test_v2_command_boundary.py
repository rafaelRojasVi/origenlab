"""Tests for the V2 human-review command boundary.

Two layers, and the split is deliberate.

**In process, always run.** The boundary's own rules — who may decide, what a request must
carry, what is refused before a connection is opened, and that the command router exposes
nothing but POST. None of it needs a database, so all of it runs in CI.

**Database-backed, opt-in.** The properties that are only true if Postgres says so:
idempotent replay, a digest mismatch, and above all that a refused command writes *nothing*,
not even its own receipt. These skip without `ORIGENLAB_V2_TEST_DSN`.

Those tests run in a **disposable database this module creates and drops** — never in a
development database. That is the safety argument for the twenty staged Gmail records of
`docs/STATUS.md` §2.7.7: these tests cannot reach them, because the database they run in did
not exist a moment ago and will not exist a moment later. It is a property of the harness,
not a cleanup convention that could be forgotten.
"""

from __future__ import annotations

import os
import pathlib
import uuid

import pytest
from fastapi.testclient import TestClient

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.main import create_app
from origenlab_api.settings import Settings
from origenlab_api.v2.commands import (
    ATTACH_CONTACT_ADDRESS,
    CONFIRM_ORGANIZATION,
    CREATE_ORGANIZATION,
    KEEP_EVIDENCE_PENDING,
    AttachContactAddressBody,
    CommandRefused,
    ConfirmOrganizationBody,
    KeepEvidencePendingBody,
    normalize_organization_name,
    request_digest,
    require_idempotency_key,
    require_writer,
)
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup

LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
RECORD_ID = "aaaaaaaa-1111-4111-8111-111111111111"
ASSERTION_ID = "bbbbbbbb-2222-4222-8222-222222222222"
ORGANIZATION_ID = "cccccccc-3333-4333-8333-333333333333"


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


class _FakeCommandRepo:
    """Records what the router asked for, and answers whatever the test needs.

    It deliberately does not imitate the database. The point of the in-process layer is the
    boundary: that a request reaches `execute` only once every rule above it has passed, and
    with exactly the fields the route promised.
    """

    def __init__(self, *, raises: CommandRefused | None = None) -> None:
        self.calls: list[dict] = []
        self.raises = raises

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        return {"command": kwargs["command_name"], "created": [], "replayed": False}


def _client(repo: _FakeCommandRepo, *, operator: OperatorIdentity | None = None) -> TestClient:
    from fastapi import FastAPI

    from origenlab_api.v2.command_routes import command_router

    app = FastAPI()
    app.include_router(command_router)
    app.state.v2_command_repository = repo
    app.state.v2_identity = LocalDevIdentity(
        LOOPBACK, _Lookup(operator if operator is not None else _operator())
    )
    return TestClient(app)


def _headers(key: str | None = "key-1") -> dict[str, str]:
    headers = {OPERATOR_EMAIL_HEADER: "operator@example.cl"}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _body(**overrides) -> dict:
    return {"source_record_id": RECORD_ID, "note": "revisado a mano", **overrides}


# ------------------------------------------------------------------ who may decide


def test_no_identity_is_401() -> None:
    client = _client(_FakeCommandRepo(), operator=None)
    response = client.post(
        "/v2/commands/keep-evidence-pending",
        json=_body(),
        headers={"Idempotency-Key": "key-1"},
    )
    assert response.status_code == 401


def test_a_viewer_may_read_the_queue_and_may_not_decide_it() -> None:
    """403, not 401. We know exactly who this is, and the answer is still no."""
    repo = _FakeCommandRepo()
    response = _client(repo, operator=_operator(role="viewer")).post(
        "/v2/commands/keep-evidence-pending", json=_body(), headers=_headers()
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "role_may_not_decide"
    assert repo.calls == []


def test_a_disabled_operator_is_refused_whatever_their_role() -> None:
    repo = _FakeCommandRepo()
    response = _client(repo, operator=_operator(role="admin", status="disabled")).post(
        "/v2/commands/keep-evidence-pending", json=_body(), headers=_headers()
    )
    assert response.status_code == 401
    assert repo.calls == []


@pytest.mark.parametrize("role", ["sales", "admin"])
def test_a_writer_role_reaches_the_repository(role: str) -> None:
    repo = _FakeCommandRepo()
    response = _client(repo, operator=_operator(role=role)).post(
        "/v2/commands/keep-evidence-pending", json=_body(), headers=_headers()
    )
    assert response.status_code == 200
    assert len(repo.calls) == 1
    assert repo.calls[0]["operator"].role == role


def test_require_writer_names_the_roles_that_may_decide() -> None:
    require_writer("sales")
    require_writer("admin")
    with pytest.raises(CommandRefused) as excinfo:
        require_writer("viewer")
    assert excinfo.value.status_code == 403


# ------------------------------------------------------------------ the four requirements


def test_a_command_without_an_idempotency_key_is_refused() -> None:
    repo = _FakeCommandRepo()
    response = _client(repo).post(
        "/v2/commands/keep-evidence-pending", json=_body(), headers=_headers(key=None)
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "idempotency_key_required"
    assert repo.calls == []


def test_a_blank_idempotency_key_is_not_a_key() -> None:
    repo = _FakeCommandRepo()
    response = _client(repo).post(
        "/v2/commands/keep-evidence-pending", json=_body(), headers=_headers(key="   ")
    )
    assert response.status_code == 400
    assert repo.calls == []


def test_an_idempotency_key_longer_than_the_column_is_refused_here() -> None:
    """A 400 that says what to fix, rather than a constraint violation from the driver."""
    with pytest.raises(CommandRefused) as excinfo:
        require_idempotency_key("k" * 201)
    assert excinfo.value.status_code == 400
    assert require_idempotency_key("  k  ") == "k"


@pytest.mark.parametrize(
    "path",
    [
        "/v2/commands/keep-evidence-pending",
        "/v2/commands/confirm-organization",
        "/v2/commands/create-organization",
        "/v2/commands/attach-contact-address",
    ],
)
def test_every_command_requires_a_reason(path: str) -> None:
    """A durable decision whose note is blank is a decision nobody can audit."""
    repo = _FakeCommandRepo()
    body = _body(
        note="   ",
        assertion_id=ASSERTION_ID,
        organization_id=ORGANIZATION_ID,
        organization_version=1,
        usage="shared_mailbox",
    )
    # Each route forbids the fields it does not take, so send only what it accepts.
    allowed = {
        "/v2/commands/keep-evidence-pending": {"source_record_id", "note"},
        "/v2/commands/confirm-organization": {
            "source_record_id", "note", "assertion_id", "organization_id", "organization_version",
        },
        "/v2/commands/create-organization": {"source_record_id", "note", "assertion_id"},
        "/v2/commands/attach-contact-address": {
            "source_record_id", "note", "assertion_id", "organization_id", "usage",
        },
    }[path]
    response = _client(repo).post(
        path, json={k: v for k, v in body.items() if k in allowed}, headers=_headers()
    )
    assert response.status_code == 422
    assert repo.calls == []


@pytest.mark.parametrize(
    "path",
    [
        "/v2/commands/keep-evidence-pending",
        "/v2/commands/confirm-organization",
        "/v2/commands/create-organization",
        "/v2/commands/attach-contact-address",
    ],
)
def test_every_command_requires_the_evidence_record(path: str) -> None:
    repo = _FakeCommandRepo()
    response = _client(repo).post(path, json={"note": "porque si"}, headers=_headers())
    assert response.status_code == 422
    assert repo.calls == []


def test_a_source_record_id_that_is_not_a_uuid_is_refused_before_the_database() -> None:
    repo = _FakeCommandRepo()
    response = _client(repo).post(
        "/v2/commands/keep-evidence-pending",
        json={"source_record_id": "not-a-uuid", "note": "x"},
        headers=_headers(),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "malformed_identifier"
    assert repo.calls == []


def test_an_unknown_field_is_refused_rather_than_ignored() -> None:
    """`extra='forbid'`: a misspelled field must not silently become a different decision."""
    repo = _FakeCommandRepo()
    response = _client(repo).post(
        "/v2/commands/keep-evidence-pending",
        json=_body(promote=True),
        headers=_headers(),
    )
    assert response.status_code == 422
    assert repo.calls == []


# ------------------------------------------------------------------ what each command takes


def test_keep_evidence_pending_carries_only_the_record_and_the_reason() -> None:
    repo = _FakeCommandRepo()
    response = _client(repo).post(
        "/v2/commands/keep-evidence-pending", json=_body(), headers=_headers()
    )
    assert response.status_code == 200
    call = repo.calls[0]
    assert call["command_name"] == KEEP_EVIDENCE_PENDING
    assert call["fields"] == {"source_record_id": RECORD_ID, "note": "revisado a mano"}
    assert call["idempotency_key"] == "key-1"


def test_confirm_organization_requires_the_version_the_operator_was_shown() -> None:
    repo = _FakeCommandRepo()
    without = _client(repo).post(
        "/v2/commands/confirm-organization",
        json=_body(assertion_id=ASSERTION_ID, organization_id=ORGANIZATION_ID),
        headers=_headers(),
    )
    assert without.status_code == 422
    assert repo.calls == []

    ok = _client(repo).post(
        "/v2/commands/confirm-organization",
        json=_body(
            assertion_id=ASSERTION_ID, organization_id=ORGANIZATION_ID, organization_version=3
        ),
        headers=_headers(),
    )
    assert ok.status_code == 200
    assert repo.calls[0]["fields"]["organization_version"] == 3


def test_create_organization_takes_no_name() -> None:
    """The name comes from the evidence, so there is nowhere in the request to put one."""
    repo = _FakeCommandRepo()
    response = _client(repo).post(
        "/v2/commands/create-organization",
        json=_body(assertion_id=ASSERTION_ID, name="Instituto Inventado"),
        headers=_headers(),
    )
    assert response.status_code == 422
    assert repo.calls == []


def test_create_organization_accepts_only_a_display_name_that_folds_to_the_same_thing() -> None:
    repo = _FakeCommandRepo()
    response = _client(repo).post(
        "/v2/commands/create-organization",
        json=_body(assertion_id=ASSERTION_ID, name_display="Instituto Del Sur"),
        headers=_headers(),
    )
    assert response.status_code == 200
    # The fold itself is checked against the assertion inside the transaction; here the
    # boundary only has to carry the operator's spelling through untouched.
    assert repo.calls[0]["fields"]["name_display"] == "Instituto Del Sur"


def test_create_organization_refuses_a_kind_outside_the_vocabulary() -> None:
    repo = _FakeCommandRepo()
    response = _client(repo).post(
        "/v2/commands/create-organization",
        json=_body(assertion_id=ASSERTION_ID, kind="university"),
        headers=_headers(),
    )
    assert response.status_code == 422
    assert repo.calls == []


def test_attach_contact_address_makes_the_operator_say_shared_mailbox() -> None:
    """`work` needs a person, and no command here creates one, so it is unreachable."""
    repo = _FakeCommandRepo()
    for usage in ("work", "personal", "unattributed"):
        response = _client(repo).post(
            "/v2/commands/attach-contact-address",
            json=_body(
                assertion_id=ASSERTION_ID, organization_id=ORGANIZATION_ID, usage=usage
            ),
            headers=_headers(),
        )
        assert response.status_code == 422, usage
    assert repo.calls == []

    ok = _client(repo).post(
        "/v2/commands/attach-contact-address",
        json=_body(
            assertion_id=ASSERTION_ID, organization_id=ORGANIZATION_ID, usage="shared_mailbox"
        ),
        headers=_headers(),
    )
    assert ok.status_code == 200
    assert repo.calls[0]["command_name"] == ATTACH_CONTACT_ADDRESS


def test_the_operator_is_taken_from_the_identity_and_never_from_the_body() -> None:
    repo = _FakeCommandRepo()
    response = _client(repo, operator=_operator(role="admin")).post(
        "/v2/commands/keep-evidence-pending",
        json=_body(operator_id="99999999-9999-4999-8999-999999999999"),
        headers=_headers(),
    )
    assert response.status_code == 422  # there is no such field to send
    assert repo.calls == []


# ------------------------------------------------------------------ the digest


def test_the_same_request_digests_the_same_way_twice() -> None:
    first = KeepEvidencePendingBody(source_record_id=RECORD_ID, note="a")
    second = KeepEvidencePendingBody(source_record_id=RECORD_ID, note="a")
    assert request_digest(KEEP_EVIDENCE_PENDING, first) == request_digest(
        KEEP_EVIDENCE_PENDING, second
    )


def test_a_different_request_digests_differently() -> None:
    base = KeepEvidencePendingBody(source_record_id=RECORD_ID, note="a")
    other_note = KeepEvidencePendingBody(source_record_id=RECORD_ID, note="b")
    other_record = KeepEvidencePendingBody(source_record_id=ORGANIZATION_ID, note="a")
    digests = {
        request_digest(KEEP_EVIDENCE_PENDING, base),
        request_digest(KEEP_EVIDENCE_PENDING, other_note),
        request_digest(KEEP_EVIDENCE_PENDING, other_record),
    }
    assert len(digests) == 3


def test_the_same_body_under_a_different_command_digests_differently() -> None:
    """Otherwise one key could replay a decision the operator never made."""
    body = KeepEvidencePendingBody(source_record_id=RECORD_ID, note="a")
    assert request_digest(KEEP_EVIDENCE_PENDING, body) != request_digest(CREATE_ORGANIZATION, body)


def test_the_digest_is_a_sha256() -> None:
    digest = request_digest(
        CONFIRM_ORGANIZATION,
        ConfirmOrganizationBody(
            source_record_id=RECORD_ID,
            note="a",
            assertion_id=ASSERTION_ID,
            organization_id=ORGANIZATION_ID,
            organization_version=1,
        ),
    )
    assert len(digest) == 64 and set(digest) <= set("0123456789abcdef")


# ------------------------------------------------------------------ name folding


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Universidad del Sur", "universidad del sur"),
        ("  Universidad   del Sur  ", "universidad del sur"),
        ("UNIVERSIDAD DEL SUR", "Universidad Del Sur"),
    ],
)
def test_folding_ignores_case_and_spacing_and_nothing_else(left: str, right: str) -> None:
    assert normalize_organization_name(left) == normalize_organization_name(right)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Universidad del Sur", "Universidad del Norte"),
        ("Laboratorio Delta", "Laboratorios Delta"),
        ("Farmadelta", "Farma Delta"),
    ],
)
def test_folding_never_decides_that_two_different_names_are_one(left: str, right: str) -> None:
    """The refusal to fuzzy-match, stated as a test rather than as a comment."""
    assert normalize_organization_name(left) != normalize_organization_name(right)


# ------------------------------------------------------------------ the router's shape


def test_the_command_router_exposes_nothing_but_post() -> None:
    from origenlab_api.v2.command_routes import command_router

    for route in command_router.routes:
        assert set(getattr(route, "methods", set())) == {"POST"}, route.path


def test_the_command_router_exposes_exactly_the_four_commands() -> None:
    from origenlab_api.v2.command_routes import command_router

    assert {route.path for route in command_router.routes} == {
        "/v2/commands/keep-evidence-pending",
        "/v2/commands/confirm-organization",
        "/v2/commands/create-organization",
        "/v2/commands/attach-contact-address",
    }


def test_the_read_router_still_has_no_write_method() -> None:
    """The reason the command boundary is a second router, asserted from this side too."""
    from origenlab_api.v2.routes import router

    for route in router.routes:
        assert set(getattr(route, "methods", set())) == {"GET"}, route.path


def test_there_is_no_command_for_anything_out_of_scope() -> None:
    """Merges, persons, prospects, marketing, campaigns, quotes and sending have no route."""
    from origenlab_api.v2.command_routes import command_router
    from origenlab_api.v2.commands import COMMAND_NAMES

    paths = " ".join(route.path for route in command_router.routes)
    for forbidden in (
        "merge", "person", "prospect", "marketing", "permission", "campaign",
        "quote", "send", "promote-all", "domain",
    ):
        assert forbidden not in paths
    assert len(COMMAND_NAMES) == 4


# ------------------------------------------------------------------ mounting


def _settings(**overrides) -> Settings:
    return Settings(sqlite_path=":memory:", **overrides)


def test_the_command_boundary_is_absent_without_its_own_switch() -> None:
    """A V2 DSN says "read this". It does not say "record decisions into it"."""
    settings = _settings(v2_database_url=LOOPBACK)
    assert settings.v2_configured() is True
    assert settings.v2_commands_configured() is False


def test_the_command_boundary_needs_a_database_as_well_as_the_switch() -> None:
    assert _settings(v2_commands_enabled=True).v2_commands_configured() is False
    assert (
        _settings(v2_database_url=LOOPBACK, v2_commands_enabled=True).v2_commands_configured()
        is True
    )


def test_an_unswitched_app_answers_404_on_a_command_path(monkeypatch) -> None:
    monkeypatch.delenv("ORIGENLAB_V2_COMMANDS_ENABLED", raising=False)
    monkeypatch.delenv("ORIGENLAB_V2_DATABASE_URL", raising=False)
    client = TestClient(create_app())
    response = client.post("/v2/commands/keep-evidence-pending", json=_body())
    assert response.status_code == 404


# ------------------------------------------------------------------ refusals reach the client


def test_a_repository_refusal_becomes_its_own_status_and_code() -> None:
    repo = _FakeCommandRepo(
        raises=CommandRefused(409, "assertion_already_resolved", "somebody decided already")
    )
    response = _client(repo).post(
        "/v2/commands/keep-evidence-pending", json=_body(), headers=_headers()
    )
    assert response.status_code == 409
    assert response.json()["detail"] == {
        "code": "assertion_already_resolved",
        "message": "somebody decided already",
    }




# --------------------------------------------------------- database-backed proofs (opt-in)
#
# These run against a **disposable database this module creates and drops**, never against a
# development database that holds real evidence. `ORIGENLAB_V2_TEST_DSN` names the cluster
# and a role that may `create database` and `set role origenlab_owner`; the database it
# points at is used for that connection and nothing else.
#
# That is the whole safety argument for the twenty staged Gmail records of `docs/STATUS.md`
# §2.7.7: these tests cannot touch them, because the database they run in does not contain
# them and did not exist a moment ago. It is not a cleanup convention that could be forgotten
# — and it is also why `crm.domain_event` being append-only, which refuses even the owner a
# DELETE, costs nothing here.

_TEST_DSN = os.environ.get("ORIGENLAB_V2_TEST_DSN", "").strip()
_needs_db = pytest.mark.skipif(
    not (_TEST_DSN and os.environ.get("ORIGENLAB_V2_API_TEST_DSN", "").strip()),
    reason="ORIGENLAB_V2_TEST_DSN and ORIGENLAB_V2_API_TEST_DSN are both required",
)

#: The commands under test run as `origenlab_api`, the role a deployment actually uses, so
#: the per-verb grants and the RLS policies are the ones being exercised. It cannot be
#: reached by `set role`: the maintenance role holds `origenlab_api` with `set_option =
#: false` on purpose, which is exactly the separation that makes this worth proving. So the
#: suite needs its login, and without one the database-backed commands skip rather than
#: quietly running as a role that can do anything.
#:
#: Locally that DSN is the one `supabase/scripts/dev_db.sh api-login` writes, outside Git.
#: Only its database name is replaced; the host, role and password are used as given.
RUNTIME_ROLE = "origenlab_api"
_API_DSN = os.environ.get("ORIGENLAB_V2_API_TEST_DSN", "").strip()

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
MIGRATIONS = REPO_ROOT / "supabase" / "migrations"

#: A hosted Supabase project provides `extensions` and its trusted extensions before any
#: migration runs. A database we create ourselves does not, so the first migration's
#: `create extension btree_gist with schema extensions` needs this first — the same platform
#: emulation `supabase/scripts/dev_db.sh` performs, and deliberately not part of the chain.
BOOTSTRAP = """
create schema if not exists extensions authorization postgres;
create extension if not exists btree_gist with schema extensions;
"""


def _swap_database(dsn: str, database: str) -> str:
    base, _, _ = dsn.rpartition("/")
    return f"{base}/{database}"


@pytest.fixture(scope="module")
def disposable_database():
    """Create `origenlab_test_<hex>`, migrate it, hand it over, drop it.

    Module-scoped because building the schema costs more than any test in it. Each test
    still creates its own evidence, so they do not share state beyond an empty schema.
    """
    import psycopg

    name = f"origenlab_test_{uuid.uuid4().hex[:8]}"
    if not MIGRATIONS.is_dir():  # pragma: no cover - the repository always has these
        pytest.skip("supabase/migrations is missing")

    with psycopg.connect(_TEST_DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(f"create database {name}")

    dsn = _swap_database(_TEST_DSN, name)
    try:
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(BOOTSTRAP)
            for path in sorted(MIGRATIONS.glob("*.sql")):
                cur.execute(path.read_text(encoding="utf-8"))
        yield dsn
    finally:
        with psycopg.connect(_TEST_DSN, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute(
                "select pg_terminate_backend(pid) from pg_stat_activity where datname = %s",
                (name,),
            )
            cur.execute(f"drop database if exists {name}")


@pytest.fixture
def seeded(disposable_database):
    """One source record, two assertions and one operator, in a database of our own.

    No cleanup: the database is dropped when the module finishes, which is both simpler than
    unwinding an append-only event stream and a stronger guarantee than doing so correctly.
    """
    import psycopg

    tag = uuid.uuid4().hex[:12]
    state = {
        "tag": tag,
        "dedupe_key": f"pytest-v2-command:{tag}",
        "org_name": f"Instituto Pytest {tag}",
        "address": f"contacto-{tag}@pytest.example",
    }
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            """
            insert into platform.operator
                (auth_user_id, email_norm, display_name, role, status)
            values (gen_random_uuid(), %s, 'Pytest Operator', 'sales', 'active')
            returning id::text
            """,
            (f"pytest-{tag}@example.cl",),
        )
        state["operator_id"] = cur.fetchone()[0]
        cur.execute(
            """
            insert into evidence.source_record (kind, dedupe_key, payload, source_uri)
            values ('gmail_message', %s, %s::jsonb, %s)
            returning id::text
            """,
            (
                state["dedupe_key"],
                '{"subject": "pytest", "from_domain": "pytest.example"}',
                f"gmail://msg/{tag}",
            ),
        )
        state["source_record_id"] = cur.fetchone()[0]
        for kind, value_norm, observed in (
            ("organization_name", state["org_name"].lower(), state["org_name"]),
            ("contact_address", state["address"], state["address"]),
        ):
            cur.execute(
                """
                insert into evidence.assertion
                    (source_record_id, kind, value_norm, value, resolution)
                values (%s, %s, %s, %s::jsonb, 'unresolved')
                returning id::text
                """,
                (
                    state["source_record_id"],
                    kind,
                    value_norm,
                    f'{{"observed_value": "{observed}"}}',
                ),
            )
            state[f"{kind}_assertion_id"] = cur.fetchone()[0]
    return state


def _runtime_dsn(disposable_database: str) -> str:
    """The `origenlab_api` login, pointed at the disposable database."""
    return _swap_database(_API_DSN, disposable_database.rpartition("/")[2])


def _repo(disposable_database: str):
    import psycopg

    from origenlab_api.v2.command_repository import V2CommandRepository

    dsn = _runtime_dsn(disposable_database)
    return V2CommandRepository(psycopg.connect, dsn)


def _identity(seeded) -> OperatorIdentity:
    return OperatorIdentity(
        operator_id=seeded["operator_id"],
        email_norm="pytest@example.cl",
        display_name="Pytest Operator",
        role="sales",
        status="active",
    )


def _run(repo, command_name, seeded, fields, key="k1", digest="d" * 64):
    return repo.execute(
        command_name=command_name,
        operator=_identity(seeded),
        fields={"source_record_id": seeded["source_record_id"], "note": "pytest", **fields},
        idempotency_key=key,
        digest=digest,
    )


@_needs_db
def test_the_tests_never_run_against_a_development_database(disposable_database) -> None:
    """The guard that makes every other database-backed test in this file safe."""
    for dsn in (disposable_database, _runtime_dsn(disposable_database)):
        database = dsn.rpartition("/")[2]
        assert database.startswith("origenlab_test_")
        assert database != "origenlab_dev"


@_needs_db
def test_keep_evidence_pending_records_a_decision_and_changes_no_state(
    disposable_database, seeded
) -> None:
    import psycopg

    result = _run(_repo(disposable_database), KEEP_EVIDENCE_PENDING, seeded, {})
    assert result["review_status"] == "pending"
    assert result["created"] == []

    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute(
            "select review_status from evidence.source_record where id = %s",
            (seeded["source_record_id"],),
        )
        assert cur.fetchone()[0] == "pending"
        cur.execute(
            "select count(*) from evidence.assertion "
            "where source_record_id = %s and resolution = 'unresolved'",
            (seeded["source_record_id"],),
        )
        assert cur.fetchone()[0] == 2
        cur.execute(
            "select event_type, payload->>'decision', actor_kind, payload->>'note' "
            "from crm.domain_event where aggregate_id = %s",
            (seeded["source_record_id"],),
        )
        assert cur.fetchone() == (
            "source_record.review_noted", "keep_pending", "operator", "pytest",
        )


@_needs_db
def test_create_then_attach_walks_a_record_to_reviewed(
    disposable_database, seeded
) -> None:
    """The whole intended path, end to end, on evidence this test made itself."""
    import psycopg

    repo = _repo(disposable_database)
    created = _run(
        repo,
        CREATE_ORGANIZATION,
        seeded,
        {"assertion_id": seeded["organization_name_assertion_id"], "kind": "unknown",
         "name_display": None},
        key="create",
        digest="a" * 64,
    )
    assert created["created"] == ["organization"]
    # One assertion is still open, so the record is not finished.
    assert created["review_status"] == "pending"

    attached = _run(
        repo,
        ATTACH_CONTACT_ADDRESS,
        seeded,
        {
            "assertion_id": seeded["contact_address_assertion_id"],
            "organization_id": created["organization_id"],
            "usage": "shared_mailbox",
        },
        key="attach",
        digest="b" * 64,
    )
    assert attached["created"] == ["contact_point"]
    assert attached["review_status"] == "reviewed"

    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute(
            "select name, confirmation, origin_source_record_id::text, note "
            "from crm.organization where id = %s",
            (created["organization_id"],),
        )
        name, confirmation, origin, note = cur.fetchone()
        assert name == seeded["org_name"]
        assert confirmation == "confirmed"
        assert origin == seeded["source_record_id"]
        assert note == "pytest"

        cur.execute(
            "select usage, person_id, organization_id::text from crm.contact_point "
            "where value_norm = %s",
            (seeded["address"],),
        )
        assert cur.fetchone() == ("shared_mailbox", None, created["organization_id"])

        # Nothing out of scope was created by this path. These counts are the whole
        # database, which is sound here because no fixture in this module creates any of
        # them: if one of these is ever non-zero, a command reached past its mandate.
        for table in (
            "crm.affiliation", "crm.organization_relationship", "crm.opportunity",
            "crm.quote", "crm.organization_domain", "outbound.campaign",
            "outbound.contact_control",
        ):
            cur.execute(f"select count(*) from {table}")
            assert cur.fetchone()[0] == 0, table
        # No person was created from this record. (Another test seeds one deliberately, so
        # this is scoped to the evidence rather than to the table.)
        cur.execute(
            "select count(*) from crm.person where origin_source_record_id = %s",
            (seeded["source_record_id"],),
        )
        assert cur.fetchone()[0] == 0

        # Both assertions are annotated, and neither lost what it observed.
        cur.execute(
            "select kind, resolution, resolved_kind, value_norm from evidence.assertion "
            "where source_record_id = %s order by kind",
            (seeded["source_record_id"],),
        )
        assert cur.fetchall() == [
            ("contact_address", "promoted", "contact_point", seeded["address"]),
            ("organization_name", "promoted", "organization", seeded["org_name"].lower()),
        ]

        # Every event this command wrote names the operator, the receipt and the evidence.
        cur.execute(
            "select count(*) from crm.domain_event where actor_operator_id = %s "
            "and (actor_kind <> 'operator' or command_receipt_id is null)",
            (seeded["operator_id"],),
        )
        assert cur.fetchone()[0] == 0


@_needs_db
def test_the_same_key_replays_instead_of_deciding_twice(
    disposable_database, seeded
) -> None:
    import psycopg

    repo = _repo(disposable_database)
    first = _run(repo, KEEP_EVIDENCE_PENDING, seeded, {}, key="same", digest="c" * 64)
    second = _run(repo, KEEP_EVIDENCE_PENDING, seeded, {}, key="same", digest="c" * 64)
    assert first["replayed"] is False
    assert second["replayed"] is True
    assert second["command_receipt_id"] == first["command_receipt_id"]

    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute(
            "select count(*) from crm.domain_event where aggregate_id = %s",
            (seeded["source_record_id"],),
        )
        assert cur.fetchone()[0] == 1, "a replay must not append a second event"


@_needs_db
def test_one_key_cannot_carry_two_different_requests(disposable_database, seeded) -> None:
    repo = _repo(disposable_database)
    _run(repo, KEEP_EVIDENCE_PENDING, seeded, {}, key="reused", digest="d" * 64)
    with pytest.raises(CommandRefused) as excinfo:
        _run(repo, KEEP_EVIDENCE_PENDING, seeded, {}, key="reused", digest="e" * 64)
    assert excinfo.value.status_code == 409
    assert excinfo.value.code == "idempotency_key_reused"


@_needs_db
def test_a_refused_command_writes_nothing_at_all(
    disposable_database, seeded
) -> None:
    """Rollback, including the receipt — so the key is still free afterwards.

    This is the property that makes a refusal safe to retry. If the receipt survived a
    failed command, a corrected retry under the same key would be answered from a receipt
    for a decision that never happened.
    """
    import psycopg

    repo = _repo(disposable_database)
    with pytest.raises(CommandRefused) as excinfo:
        _run(
            repo,
            CONFIRM_ORGANIZATION,
            seeded,
            {
                "assertion_id": seeded["organization_name_assertion_id"],
                "organization_id": str(uuid.uuid4()),
                "organization_version": 1,
            },
            key="refused",
            digest="f" * 64,
        )
    assert excinfo.value.status_code == 404

    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute(
            "select count(*) from platform.command_receipt where operator_id = %s",
            (seeded["operator_id"],),
        )
        assert cur.fetchone()[0] == 0, "a refused command left its receipt behind"
        cur.execute(
            "select count(*) from crm.domain_event where actor_operator_id = %s",
            (seeded["operator_id"],),
        )
        assert cur.fetchone()[0] == 0
        cur.execute(
            "select count(*) from evidence.assertion "
            "where source_record_id = %s and resolution <> 'unresolved'",
            (seeded["source_record_id"],),
        )
        assert cur.fetchone()[0] == 0

    # And the key is free, so the corrected request goes through.
    ok = _run(repo, KEEP_EVIDENCE_PENDING, seeded, {}, key="refused", digest="f" * 64)
    assert ok["replayed"] is False


@_needs_db
def test_a_name_that_is_not_an_exact_match_is_refused(
    disposable_database, seeded
) -> None:
    """The anti-fuzzy rule, proven against a real neighbouring organization."""
    import psycopg

    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "insert into crm.organization (kind, name, confirmation) "
            "values ('unknown', %s, 'machine_proposed') returning id::text, version",
            (seeded["org_name"] + " Norte",),
        )
        neighbour_id, version = cur.fetchone()

    with pytest.raises(CommandRefused) as excinfo:
        _run(
            _repo(disposable_database),
            CONFIRM_ORGANIZATION,
            seeded,
            {
                "assertion_id": seeded["organization_name_assertion_id"],
                "organization_id": neighbour_id,
                "organization_version": version,
            },
            key="near",
            digest="1" * 64,
        )
    assert excinfo.value.code == "organization_name_is_not_an_exact_match"


@_needs_db
def test_confirming_an_exact_match_links_without_creating(
    disposable_database, seeded
) -> None:
    import psycopg

    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "insert into crm.organization (kind, name, confirmation) "
            "values ('unknown', %s, 'machine_proposed') returning id::text, version",
            (seeded["org_name"],),
        )
        organization_id, version = cur.fetchone()

    result = _run(
        _repo(disposable_database),
        CONFIRM_ORGANIZATION,
        seeded,
        {
            "assertion_id": seeded["organization_name_assertion_id"],
            "organization_id": organization_id,
            "organization_version": version,
        },
        key="confirm",
        digest="a1" * 32,
    )
    assert result["created"] == []

    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute(
            "select count(*) from crm.organization where lower(name) = %s",
            (seeded["org_name"].lower(),),
        )
        assert cur.fetchone()[0] == 1, "confirming must not create a second organization"
        cur.execute(
            "select confirmation, version, confirmed_by_operator_id::text "
            "from crm.organization where id = %s",
            (organization_id,),
        )
        assert cur.fetchone() == ("confirmed", version + 1, seeded["operator_id"])
        cur.execute(
            "select resolution, resolved_kind, resolved_id::text from evidence.assertion "
            "where id = %s",
            (seeded["organization_name_assertion_id"],),
        )
        assert cur.fetchone() == ("linked", "organization", organization_id)


@_needs_db
def test_a_stale_version_is_refused(disposable_database, seeded) -> None:
    import psycopg

    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "insert into crm.organization (kind, name, confirmation) "
            "values ('unknown', %s, 'machine_proposed') returning id::text",
            (seeded["org_name"],),
        )
        organization_id = cur.fetchone()[0]

    with pytest.raises(CommandRefused) as excinfo:
        _run(
            _repo(disposable_database),
            CONFIRM_ORGANIZATION,
            seeded,
            {
                "assertion_id": seeded["organization_name_assertion_id"],
                "organization_id": organization_id,
                "organization_version": 99,
            },
            key="stale",
            digest="2" * 64,
        )
    assert excinfo.value.code == "organization_version_conflict"


@_needs_db
def test_an_assertion_from_another_record_cannot_be_decided_here(disposable_database, seeded) -> None:
    with pytest.raises(CommandRefused) as excinfo:
        _run(
            _repo(disposable_database),
            CREATE_ORGANIZATION,
            seeded,
            {"assertion_id": str(uuid.uuid4()), "kind": "unknown", "name_display": None},
            key="foreign",
            digest="3" * 64,
        )
    assert excinfo.value.code == "assertion_not_found"


@_needs_db
def test_the_wrong_kind_of_assertion_is_refused(disposable_database, seeded) -> None:
    """An address is not a name, and neither command will pretend otherwise."""
    with pytest.raises(CommandRefused) as excinfo:
        _run(
            _repo(disposable_database),
            CREATE_ORGANIZATION,
            seeded,
            {"assertion_id": seeded["contact_address_assertion_id"], "kind": "unknown",
             "name_display": None},
            key="wrongkind",
            digest="b1" * 32,
        )
    assert excinfo.value.code == "assertion_wrong_kind"


@_needs_db
def test_an_already_resolved_assertion_is_not_decided_twice(disposable_database, seeded) -> None:
    repo = _repo(disposable_database)
    _run(
        repo,
        CREATE_ORGANIZATION,
        seeded,
        {"assertion_id": seeded["organization_name_assertion_id"], "kind": "unknown",
         "name_display": None},
        key="first",
        digest="4" * 64,
    )
    with pytest.raises(CommandRefused) as excinfo:
        _run(
            repo,
            CREATE_ORGANIZATION,
            seeded,
            {"assertion_id": seeded["organization_name_assertion_id"], "kind": "unknown",
             "name_display": None},
            key="second",
            digest="5" * 64,
        )
    assert excinfo.value.code == "assertion_already_resolved"


@_needs_db
def test_a_duplicate_name_points_at_confirm_instead_of_creating_a_second_row(
    disposable_database, seeded
) -> None:
    import psycopg

    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "insert into crm.organization (kind, name, confirmation) "
            "values ('unknown', %s, 'machine_proposed')",
            (seeded["org_name"],),
        )
    with pytest.raises(CommandRefused) as excinfo:
        _run(
            _repo(disposable_database),
            CREATE_ORGANIZATION,
            seeded,
            {"assertion_id": seeded["organization_name_assertion_id"], "kind": "unknown",
             "name_display": None},
            key="dup",
            digest="6" * 64,
        )
    assert excinfo.value.code == "organization_already_exists"


@_needs_db
def test_a_display_name_the_message_never_stated_is_refused(disposable_database, seeded) -> None:
    """`name_display` restores letter case. It does not rename the evidence."""
    with pytest.raises(CommandRefused) as excinfo:
        _run(
            _repo(disposable_database),
            CREATE_ORGANIZATION,
            seeded,
            {
                "assertion_id": seeded["organization_name_assertion_id"],
                "kind": "unknown",
                "name_display": "Alguna Otra Institución",
            },
            key="rename",
            digest="c1" * 32,
        )
    assert excinfo.value.code == "name_display_does_not_match_the_assertion"


@_needs_db
def test_an_address_owned_by_a_person_is_never_taken_for_a_desk(
    disposable_database, seeded
) -> None:
    import psycopg

    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "insert into crm.person (display_name, confirmation) "
            "values ('Pytest Person', 'confirmed') returning id::text"
        )
        person_id = cur.fetchone()[0]
        cur.execute(
            "insert into crm.contact_point (kind, value_norm, value_display, person_id, "
            "usage, confirmation) values ('email', %s, %s, %s, 'personal', 'confirmed')",
            (seeded["address"], seeded["address"], person_id),
        )
        cur.execute(
            "insert into crm.organization (kind, name, confirmation) "
            "values ('unknown', %s, 'machine_proposed') returning id::text",
            (seeded["org_name"],),
        )
        organization_id = cur.fetchone()[0]

    with pytest.raises(CommandRefused) as excinfo:
        _run(
            _repo(disposable_database),
            ATTACH_CONTACT_ADDRESS,
            seeded,
            {
                "assertion_id": seeded["contact_address_assertion_id"],
                "organization_id": organization_id,
                "usage": "shared_mailbox",
            },
            key="person",
            digest="7" * 64,
        )
    assert excinfo.value.code == "contact_point_belongs_to_a_person"


@_needs_db
def test_an_existing_unattributed_address_is_attached_rather_than_duplicated(
    disposable_database, seeded
) -> None:
    """The common case: the historical import already knows the address, owner unknown."""
    import psycopg

    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "insert into crm.contact_point (kind, value_norm, value_display, usage, "
            "confirmation) values ('email', %s, %s, 'unattributed', 'machine_proposed') "
            "returning id::text",
            (seeded["address"], seeded["address"]),
        )
        contact_point_id = cur.fetchone()[0]
        cur.execute(
            "insert into crm.organization (kind, name, confirmation) "
            "values ('unknown', %s, 'machine_proposed') returning id::text",
            (seeded["org_name"],),
        )
        organization_id = cur.fetchone()[0]

    result = _run(
        _repo(disposable_database),
        ATTACH_CONTACT_ADDRESS,
        seeded,
        {
            "assertion_id": seeded["contact_address_assertion_id"],
            "organization_id": organization_id,
            "usage": "shared_mailbox",
        },
        key="attach-existing",
        digest="d1" * 32,
    )
    assert result["created"] == [], "an existing channel must not be duplicated"
    assert result["contact_point_id"] == contact_point_id

    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from crm.contact_point where value_norm = %s",
                    (seeded["address"],))
        assert cur.fetchone()[0] == 1
        cur.execute(
            "select usage, organization_id::text, confirmation from crm.contact_point "
            "where id = %s",
            (contact_point_id,),
        )
        assert cur.fetchone() == ("shared_mailbox", organization_id, "confirmed")
        cur.execute(
            "select resolution from evidence.assertion where id = %s",
            (seeded["contact_address_assertion_id"],),
        )
        assert cur.fetchone()[0] == "linked"


@_needs_db
def test_an_address_another_organization_already_owns_is_refused(
    disposable_database, seeded
) -> None:
    import psycopg

    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "insert into crm.organization (kind, name, confirmation) "
            "values ('unknown', 'Otra Institucion Pytest', 'confirmed') returning id::text"
        )
        other_id = cur.fetchone()[0]
        cur.execute(
            "insert into crm.contact_point (kind, value_norm, value_display, organization_id, "
            "usage, confirmation) values ('email', %s, %s, %s, 'shared_mailbox', 'confirmed')",
            (seeded["address"], seeded["address"], other_id),
        )
        cur.execute(
            "insert into crm.organization (kind, name, confirmation) "
            "values ('unknown', %s, 'machine_proposed') returning id::text",
            (seeded["org_name"],),
        )
        organization_id = cur.fetchone()[0]

    with pytest.raises(CommandRefused) as excinfo:
        _run(
            _repo(disposable_database),
            ATTACH_CONTACT_ADDRESS,
            seeded,
            {
                "assertion_id": seeded["contact_address_assertion_id"],
                "organization_id": organization_id,
                "usage": "shared_mailbox",
            },
            key="taken",
            digest="e1" * 32,
        )
    assert excinfo.value.code == "contact_point_attached_elsewhere"


@_needs_db
def test_a_quarantined_record_is_not_promotable(
    disposable_database, seeded
) -> None:
    import psycopg

    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "update evidence.source_record set is_quarantined = true, "
            "quarantine_reason = 'pytest', quarantined_at = now() where id = %s",
            (seeded["source_record_id"],),
        )
    with pytest.raises(CommandRefused) as excinfo:
        _run(_repo(disposable_database), KEEP_EVIDENCE_PENDING, seeded, {}, key="q", digest="8" * 64)
    assert excinfo.value.code == "source_record_quarantined"


@_needs_db
def test_evidence_survives_every_command_unchanged(
    disposable_database, seeded
) -> None:
    """What the mailbox said stays what the mailbox said.

    The record's payload and each assertion's `kind` and `value_norm` are compared before
    and after the full promotion path. Only the resolution columns are allowed to move.
    """
    import psycopg

    def evidence_snapshot():
        with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
            cur.execute(
                "select payload::text, dedupe_key, source_uri, kind "
                "from evidence.source_record where id = %s",
                (seeded["source_record_id"],),
            )
            record = cur.fetchone()
            cur.execute(
                "select id::text, kind, value_norm, value::text from evidence.assertion "
                "where source_record_id = %s order by id",
                (seeded["source_record_id"],),
            )
            return record, cur.fetchall()

    before = evidence_snapshot()
    repo = _repo(disposable_database)
    created = _run(
        repo,
        CREATE_ORGANIZATION,
        seeded,
        {"assertion_id": seeded["organization_name_assertion_id"], "kind": "institution",
         "name_display": None},
        key="ev1",
        digest="9" * 64,
    )
    _run(
        repo,
        ATTACH_CONTACT_ADDRESS,
        seeded,
        {
            "assertion_id": seeded["contact_address_assertion_id"],
            "organization_id": created["organization_id"],
            "usage": "shared_mailbox",
        },
        key="ev2",
        digest="a" * 63 + "b",
    )
    assert evidence_snapshot() == before


@_needs_db
def test_the_unprivileged_role_cannot_rewrite_what_a_message_said(disposable_database) -> None:
    """Grants, not good intentions.

    `origenlab_api` may annotate an assertion's resolution and may not touch its
    `value_norm`, insert a new assertion or delete one. A future command that tried would be
    stopped by Postgres, which is why "evidence is never destroyed" is a property of the
    deployment rather than of this code.
    """
    import psycopg

    with psycopg.connect(_runtime_dsn(disposable_database)) as conn, conn.cursor() as cur:
        cur.execute("select current_user")
        assert cur.fetchone()[0] == RUNTIME_ROLE
        for statement in (
            "update evidence.assertion set value_norm = 'rewritten'",
            "delete from evidence.assertion",
            "insert into evidence.assertion (source_record_id, kind, value_norm) "
            "values (gen_random_uuid(), 'organization_name', 'invented')",
            "delete from evidence.source_record",
            "update evidence.source_record set payload = '{}'::jsonb",
            "delete from crm.domain_event",
            "update crm.domain_event set payload = '{}'::jsonb",
        ):
            with pytest.raises(psycopg.Error):
                cur.execute(statement)
            conn.rollback()
