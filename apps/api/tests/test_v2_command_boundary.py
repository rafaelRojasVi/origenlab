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
    ATTACHABLE_USAGE,
    ATTRIBUTE_SENDER_ORGANIZATION,
    CONFIRM_ORGANIZATION,
    CREATE_ORGANIZATION,
    KEEP_EVIDENCE_PENDING,
    AttachContactAddressBody,
    AttributeSenderOrganizationBody,
    CommandRefused,
    ConfirmOrganizationBody,
    KeepEvidencePendingBody,
    address_names_a_desk,
    require_override_for_a_named_address,
    validated,
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
        "/v2/commands/attribute-sender-organization",
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
        organization_assertion_id=ASSERTION_ID,
        address_assertion_id=ADDRESS_ASSERTION_ID,
        target="existing",
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
        "/v2/commands/attribute-sender-organization": {
            "source_record_id", "note", "organization_assertion_id", "address_assertion_id",
            "target", "organization_id", "organization_version", "usage",
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
        "/v2/commands/attribute-sender-organization",
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


def test_the_command_router_exposes_exactly_the_five_commands() -> None:
    from origenlab_api.v2.command_routes import command_router

    assert {route.path for route in command_router.routes} == {
        "/v2/commands/keep-evidence-pending",
        "/v2/commands/confirm-organization",
        "/v2/commands/create-organization",
        "/v2/commands/attach-contact-address",
        "/v2/commands/attribute-sender-organization",
    }


def test_the_read_router_still_has_no_write_method() -> None:
    """The reason the command boundary is a second router, asserted from this side too."""
    from origenlab_api.v2.routes import router

    for route in router.routes:
        assert set(getattr(route, "methods", set())) == {"GET"}, route.path


def test_there_is_no_command_for_anything_out_of_scope() -> None:
    """Merges, persons, prospects, marketing, campaigns, quotes and sending have no route.

    The comparison is over the **words** of a path, not its characters. A substring test
    called `attribute-sender-organization` a send route because `sender` contains `send`,
    which is the false positive that makes a guard get relaxed. Word-wise it is stricter
    where it matters: a real `/v2/commands/send-campaign` still fails, and so would
    `create-person`, while a word that merely contains a forbidden one does not.
    """
    from origenlab_api.v2.command_routes import command_router
    from origenlab_api.v2.commands import COMMAND_NAMES

    words = {
        word
        for route in command_router.routes
        for segment in route.path.split("/")
        for word in segment.split("-")
    }
    paths = " ".join(route.path for route in command_router.routes)
    for forbidden in (
        "merge", "person", "persons", "prospect", "marketing", "permission", "campaign",
        "quote", "send", "sends", "domain", "task",
    ):
        assert forbidden not in words, f"{forbidden!r} is a word in {paths}"
    # Multi-word shapes stay substring checks: there is no single word to look for.
    for forbidden in ("promote-all", "bulk", "auto"):
        assert forbidden not in paths
    assert len(COMMAND_NAMES) == 5


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




# ------------------------------------------------- attributing a sender: the rules in process
#
# The composed command exists because a message that names a supplier *and* the end customer
# has exactly one sender, and the four single commands made the operator say so in two
# transactions. These tests are about the half of that which needs no database: a request
# that says two things at once is refused rather than reinterpreted.

ADDRESS_ASSERTION_ID = "dddddddd-4444-4444-8444-444444444444"


def _attribution(**overrides) -> AttributeSenderOrganizationBody:
    body = {
        "source_record_id": RECORD_ID,
        "note": "el remitente es de esta institución",
        "organization_assertion_id": ASSERTION_ID,
        "address_assertion_id": ADDRESS_ASSERTION_ID,
        "target": "existing",
        "organization_id": ORGANIZATION_ID,
        "organization_version": 1,
        "usage": "shared_mailbox",
    }
    body.update(overrides)
    return AttributeSenderOrganizationBody(**body)


def test_attributing_an_existing_institution_validates() -> None:
    fields = validated(ATTRIBUTE_SENDER_ORGANIZATION, _attribution())
    assert fields["target"] == "existing"
    assert fields["organization_id"] == ORGANIZATION_ID
    assert fields["organization_version"] == 1
    assert fields["organization_assertion_id"] == ASSERTION_ID
    assert fields["address_assertion_id"] == ADDRESS_ASSERTION_ID
    assert "kind" not in fields and "name_display" not in fields


def test_attributing_a_new_institution_validates_and_carries_no_organization_id() -> None:
    fields = validated(
        ATTRIBUTE_SENDER_ORGANIZATION,
        _attribution(target="new", organization_id=None, organization_version=None),
    )
    assert fields["target"] == "new"
    assert fields["kind"] == "unknown"
    assert fields["name_display"] is None
    assert "organization_id" not in fields


def test_an_existing_target_without_a_version_is_refused() -> None:
    with pytest.raises(CommandRefused) as exc:
        validated(ATTRIBUTE_SENDER_ORGANIZATION, _attribution(organization_version=None))
    assert exc.value.code == "existing_organization_requires_id_and_version"
    assert exc.value.status_code == 422


def test_an_existing_target_may_not_also_name_the_organization() -> None:
    """Choosing a row and typing a name are two different claims; both is neither."""
    with pytest.raises(CommandRefused) as exc:
        validated(ATTRIBUTE_SENDER_ORGANIZATION, _attribution(name_display="Otra Cosa"))
    assert exc.value.code == "name_display_is_only_for_a_new_organization"


def test_a_new_target_may_not_also_point_at_an_existing_organization() -> None:
    with pytest.raises(CommandRefused) as exc:
        validated(ATTRIBUTE_SENDER_ORGANIZATION, _attribution(target="new"))
    assert exc.value.code == "new_organization_takes_no_organization_id"


def test_the_institution_and_the_address_must_be_two_different_assertions() -> None:
    with pytest.raises(CommandRefused) as exc:
        validated(
            ATTRIBUTE_SENDER_ORGANIZATION,
            _attribution(address_assertion_id=ASSERTION_ID),
        )
    assert exc.value.code == "one_assertion_cannot_be_both"


def test_an_attribution_still_needs_a_reason() -> None:
    with pytest.raises(Exception):
        _attribution(note="   ")


def test_an_attribution_cannot_ask_for_a_personal_mailbox() -> None:
    """`work` implies a person, and no command here creates one."""
    with pytest.raises(Exception):
        _attribution(usage="work")


def test_an_attribution_digests_differently_from_its_two_halves() -> None:
    """A composed decision is not the same request as either command it replaces."""
    attribution = request_digest(ATTRIBUTE_SENDER_ORGANIZATION, _attribution())
    confirm = request_digest(
        CONFIRM_ORGANIZATION,
        ConfirmOrganizationBody(
            source_record_id=RECORD_ID,
            note="el remitente es de esta institución",
            assertion_id=ASSERTION_ID,
            organization_id=ORGANIZATION_ID,
            organization_version=1,
        ),
    )
    assert attribution != confirm


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

from protected_databases import (  # noqa: E402
    assert_connection_is_disposable,
    assert_not_protected,
)

_TEST_DSN = assert_not_protected(
    os.environ.get("ORIGENLAB_V2_TEST_DSN", "").strip(),
    variable="ORIGENLAB_V2_TEST_DSN",
)
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
_API_DSN = assert_not_protected(
    os.environ.get("ORIGENLAB_V2_API_TEST_DSN", "").strip(),
    variable="ORIGENLAB_V2_API_TEST_DSN",
)

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
        with psycopg.connect(dsn, autocommit=True) as conn:
            # Ask the server, before any statement runs, which database this actually is.
            # The migration chain is applied below WITHOUT writing ledger rows, which is
            # correct for a throwaway database and corrupting for a real one — on 2026-09-22
            # `origenlab_dev` ended up carrying a migration's DDL with no ledger row to
            # match. A name computed by string surgery is not evidence; this is.
            reached = assert_connection_is_disposable(conn)
            assert reached == name, (
                f"expected to reach the disposable database {name!r}, reached {reached!r}"
            )
            with conn.cursor() as cur:
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


# ------------------------------------- attributing a sender, against a real Postgres
#
# **Why the fixtures below are fictitious.** These are modelled on two real records in the
# clean room -- a message naming a manufacturer *and* the university that will use the
# equipment, and a message naming one company that does not exist in the CRM yet -- but this
# repository is public and the real senders are real people. So the *shape* is committed and
# the *values* are reserved (`.example`, RFC 2606). The same code is rehearsed against the
# real rows by `supabase/scripts/rehearse_attribution.py`, which reads them from the clean
# room at run time and commits nothing.


@pytest.fixture
def two_named_institutions(disposable_database):
    """Two messages of one thread, each naming the same two institutions.

    One institution already exists in the CRM under exactly the asserted name and is
    `machine_proposed`; the other exists nowhere. Each message has a different sender, and
    the sender of each belongs to a *different* one of the two. That is the whole difficulty
    the composed command was built for, and it cannot be reproduced with one record.
    """
    import psycopg

    tag = uuid.uuid4().hex[:12]
    state = {
        "tag": tag,
        "existing_name": f"Universidad Ejemplo {tag}",
        "new_name": f"Ultrasonics Ejemplo {tag}",
        "supplier_address": f"ventas-{tag}@proveedor.example",
        "university_address": f"compras-{tag}@universidad.example",
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
            (f"pytest-attr-{tag}@example.cl",),
        )
        state["operator_id"] = cur.fetchone()[0]

        # The institution the CRM already proposed, exactly as the clean room holds it:
        # machine_proposed, version 1, never confirmed by anyone.
        cur.execute(
            "insert into crm.organization (kind, name, confirmation) "
            "values ('unknown', %s, 'machine_proposed') returning id::text, version",
            (state["existing_name"],),
        )
        state["existing_organization_id"], state["existing_version"] = cur.fetchone()

        for side, address in (
            ("supplier", state["supplier_address"]),
            ("university", state["university_address"]),
        ):
            cur.execute(
                """
                insert into evidence.source_record (kind, dedupe_key, payload, source_uri)
                values ('gmail_message', %s, %s::jsonb, %s)
                returning id::text
                """,
                (
                    f"pytest-attr:{tag}:{side}",
                    '{"subject": "pytest", "from_domain": "proveedor.example"}',
                    f"gmail://msg/{tag}-{side}",
                ),
            )
            record_id = cur.fetchone()[0]
            state[f"{side}_record_id"] = record_id
            for kind, value_norm, observed in (
                ("organization_name", state["new_name"].lower(), state["new_name"]),
                ("organization_name", state["existing_name"].lower(), state["existing_name"]),
                ("contact_address", address, address),
            ):
                cur.execute(
                    """
                    insert into evidence.assertion
                        (source_record_id, kind, value_norm, value, resolution)
                    values (%s, %s, %s, %s::jsonb, 'unresolved')
                    returning id::text
                    """,
                    (record_id, kind, value_norm, f'{{"observed_value": "{observed}"}}'),
                )
                label = "address" if kind == "contact_address" else (
                    "new_org" if value_norm == state["new_name"].lower() else "existing_org"
                )
                state[f"{side}_{label}_assertion_id"] = cur.fetchone()[0]
    return state


@pytest.fixture
def one_unknown_institution(disposable_database):
    """One message naming one institution the CRM has never recorded, plus its sender."""
    import psycopg

    tag = uuid.uuid4().hex[:12]
    state = {
        "tag": tag,
        "org_name": f"Agriscience Ejemplo {tag}",
        "address": f"ventas-{tag}@agro.example",
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
            (f"pytest-one-{tag}@example.cl",),
        )
        state["operator_id"] = cur.fetchone()[0]
        cur.execute(
            """
            insert into evidence.source_record (kind, dedupe_key, payload, source_uri)
            values ('gmail_message', %s, %s::jsonb, %s)
            returning id::text
            """,
            (
                f"pytest-one:{tag}",
                '{"subject": "pytest", "from_domain": "agro.example"}',
                f"gmail://msg/{tag}",
            ),
        )
        state["source_record_id"] = cur.fetchone()[0]
        for kind, value_norm, observed, label in (
            ("organization_name", state["org_name"].lower(), state["org_name"], "org"),
            ("contact_address", state["address"], state["address"], "address"),
        ):
            cur.execute(
                """
                insert into evidence.assertion
                    (source_record_id, kind, value_norm, value, resolution)
                values (%s, %s, %s, %s::jsonb, 'unresolved')
                returning id::text
                """,
                (state["source_record_id"], kind, value_norm, f'{{"observed_value": "{observed}"}}'),
            )
            state[f"{label}_assertion_id"] = cur.fetchone()[0]
    return state


def _attribute(repo, state, record_key, fields, key, digest):
    return repo.execute(
        command_name=ATTRIBUTE_SENDER_ORGANIZATION,
        operator=_identity(state),
        fields={"source_record_id": state[record_key], "note": "pytest", **fields},
        idempotency_key=key,
        digest=digest,
    )


@_needs_db
def test_creating_and_attaching_happen_in_one_transaction(
    disposable_database, one_unknown_institution
) -> None:
    """The Corteva shape: an institution the CRM never had, and the sender's mailbox on it.

    One call, one receipt. Before this command the same outcome took `create-organization`
    and then `attach-contact-address`, and an operator who stopped between them was left
    holding an institution no address pointed at.
    """
    import psycopg

    state = one_unknown_institution
    result = _attribute(
        _repo(disposable_database),
        state,
        "source_record_id",
        {
            "organization_assertion_id": state["org_assertion_id"],
            "address_assertion_id": state["address_assertion_id"],
            "target": "new",
            "kind": "unknown",
            "name_display": None,
            "usage": "shared_mailbox",
        },
        key=f"attr-new-{state['tag']}",
        digest="1" * 64,
    )
    assert result["created"] == ["organization", "contact_point"]
    assert result["assertions_left_unresolved"] == 0
    assert result["review_status"] == "reviewed"

    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute(
            "select name, confirmation, origin_source_record_id::text from crm.organization "
            "where id = %s",
            (result["organization_id"],),
        )
        name, confirmation, origin = cur.fetchone()
        # The name is the assertion's, letter for letter. Nothing here read a domain.
        assert name == state["org_name"]
        assert confirmation == "confirmed"
        assert origin == state["source_record_id"]

        cur.execute(
            "select value_norm, organization_id::text, person_id, usage, confirmation "
            "from crm.contact_point where id = %s",
            (result["contact_point_id"],),
        )
        value_norm, organization_id, person_id, usage, cp_confirmation = cur.fetchone()
        assert value_norm == state["address"]
        assert organization_id == result["organization_id"]
        assert person_id is None
        assert usage == "shared_mailbox"
        assert cp_confirmation == "confirmed"

        # One receipt for the whole decision, not two.
        cur.execute("select count(*) from platform.command_receipt where idempotency_key = %s",
                    (f"attr-new-{state['tag']}",))
        assert cur.fetchone()[0] == 1


@_needs_db
def test_an_attribution_creates_no_person_and_registers_no_domain(
    disposable_database, one_unknown_institution
) -> None:
    """The two things a domain-shaped guess would produce, counted before and after."""
    import psycopg

    state = one_unknown_institution

    def counts():
        with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
            cur.execute(
                "select (select count(*) from crm.person), "
                "(select count(*) from crm.organization_domain), "
                "(select count(*) from crm.affiliation), "
                "(select count(*) from crm.opportunity), "
                "(select count(*) from crm.quote), "
                "(select count(*) from outbound.campaign_recipient)"
            )
            return cur.fetchone()

    before = counts()
    _attribute(
        _repo(disposable_database),
        state,
        "source_record_id",
        {
            "organization_assertion_id": state["org_assertion_id"],
            "address_assertion_id": state["address_assertion_id"],
            "target": "new",
            "kind": "unknown",
            "name_display": None,
            "usage": "shared_mailbox",
        },
        key=f"attr-nothing-{state['tag']}",
        digest="2" * 64,
    )
    assert counts() == before


@_needs_db
def test_only_the_chosen_institution_resolves_and_the_other_stays_open(
    disposable_database, two_named_institutions
) -> None:
    """The UACh/Hielscher shape, from the supplier's side.

    The message names two institutions. The operator picks the one the *sender* belongs to.
    The other assertion is untouched, and because it is still unresolved the record stays in
    the queue — which is the state the operator chose, not a leftover.
    """
    import psycopg

    state = two_named_institutions
    result = _attribute(
        _repo(disposable_database),
        state,
        "supplier_record_id",
        {
            "organization_assertion_id": state["supplier_new_org_assertion_id"],
            "address_assertion_id": state["supplier_address_assertion_id"],
            "target": "new",
            "kind": "unknown",
            "name_display": None,
            "usage": "shared_mailbox",
        },
        key=f"attr-supplier-{state['tag']}",
        digest="3" * 64,
    )
    assert result["assertions_left_unresolved"] == 1
    assert result["review_status"] == "pending"

    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute(
            "select resolution, resolved_kind, resolved_id::text from evidence.assertion "
            "where id = %s",
            (state["supplier_new_org_assertion_id"],),
        )
        assert cur.fetchone() == ("promoted", "organization", result["organization_id"])

        # The other institution this message names: untouched, in every column.
        cur.execute(
            "select resolution, resolved_kind, resolved_id, resolved_by_operator_id "
            "from evidence.assertion where id = %s",
            (state["supplier_existing_org_assertion_id"],),
        )
        assert cur.fetchone() == ("unresolved", None, None, None)

        # And it was not confirmed as a side effect either.
        cur.execute(
            "select confirmation, version from crm.organization where id = %s",
            (state["existing_organization_id"],),
        )
        assert cur.fetchone() == ("machine_proposed", 1)

        cur.execute(
            "select review_status from evidence.source_record where id = %s",
            (state["supplier_record_id"],),
        )
        assert cur.fetchone()[0] == "pending"


@_needs_db
def test_each_sender_takes_its_own_institution(
    disposable_database, two_named_institutions
) -> None:
    """Two senders, two institutions, from the same pair of asserted names.

    The supplier's address goes to the institution that did not exist; the university's goes
    to the one that did, confirming it. Nothing about the first decision reached the second.
    """
    import psycopg

    state = two_named_institutions
    repo = _repo(disposable_database)

    supplier = _attribute(
        repo, state, "supplier_record_id",
        {
            "organization_assertion_id": state["supplier_new_org_assertion_id"],
            "address_assertion_id": state["supplier_address_assertion_id"],
            "target": "new", "kind": "unknown", "name_display": None,
            "usage": "shared_mailbox",
        },
        key=f"pair-supplier-{state['tag']}", digest="4" * 64,
    )
    university = _attribute(
        repo, state, "university_record_id",
        {
            "organization_assertion_id": state["university_existing_org_assertion_id"],
            "address_assertion_id": state["university_address_assertion_id"],
            "target": "existing",
            "organization_id": state["existing_organization_id"],
            "organization_version": state["existing_version"],
            "usage": "shared_mailbox",
        },
        key=f"pair-university-{state['tag']}", digest="5" * 64,
    )
    assert supplier["organization_id"] != university["organization_id"]
    assert university["organization_id"] == state["existing_organization_id"]
    assert university["created"] == ["contact_point"]
    # Confirming a machine proposal moves it forward, and the response says where to.
    assert university["organization_version"] == state["existing_version"] + 1

    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute(
            "select value_norm, organization_id::text from crm.contact_point "
            "where value_norm in (%s, %s) order by value_norm",
            (state["supplier_address"], state["university_address"]),
        )
        rows = dict(cur.fetchall())
        assert rows[state["supplier_address"]] == supplier["organization_id"]
        assert rows[state["university_address"]] == university["organization_id"]

        cur.execute(
            "select confirmation, version from crm.organization where id = %s",
            (state["existing_organization_id"],),
        )
        assert cur.fetchone() == ("confirmed", state["existing_version"] + 1)


@_needs_db
def test_one_address_can_never_be_claimed_by_both_named_institutions(
    disposable_database, two_named_institutions
) -> None:
    """The refusal the whole case turns on.

    Once the supplier's mailbox belongs to the supplier, the *other* institution the same
    message names cannot take it. An address that meant two institutions at once would make
    every later read of the CRM ambiguous, so the second attribution is refused rather than
    merged, overwritten or silently ignored.
    """
    import psycopg

    from origenlab_api.v2.commands import CommandRefused

    state = two_named_institutions
    repo = _repo(disposable_database)
    _attribute(
        repo, state, "supplier_record_id",
        {
            "organization_assertion_id": state["supplier_new_org_assertion_id"],
            "address_assertion_id": state["supplier_address_assertion_id"],
            "target": "new", "kind": "unknown", "name_display": None,
            "usage": "shared_mailbox",
        },
        key=f"claim-first-{state['tag']}", digest="6" * 64,
    )

    # The same address, on the same message, to the other institution it names.
    with pytest.raises(CommandRefused) as exc:
        _attribute(
            repo, state, "supplier_record_id",
            {
                "organization_assertion_id": state["supplier_existing_org_assertion_id"],
                "address_assertion_id": state["supplier_address_assertion_id"],
                "target": "existing",
                "organization_id": state["existing_organization_id"],
                "organization_version": state["existing_version"],
                "usage": "shared_mailbox",
            },
            key=f"claim-second-{state['tag']}", digest="7" * 64,
        )
    # It is refused for the address, having already passed every check about the assertion.
    assert exc.value.code == "assertion_already_resolved"
    assert exc.value.status_code == 409

    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute(
            "select confirmation from crm.organization where id = %s",
            (state["existing_organization_id"],),
        )
        assert cur.fetchone()[0] == "machine_proposed"


@_needs_db
def test_a_second_message_cannot_move_an_address_to_another_institution(
    disposable_database, two_named_institutions
) -> None:
    """The same refusal, reached the only way that gets past the resolved-assertion check.

    The university's message asserts the supplier's address is *not* in it, so this uses the
    university's own address and tries to hand it to the supplier's institution after the
    university already has it. `contact_point_attached_elsewhere` is the guard that makes one
    mailbox belong to one institution across messages, not just within one.
    """
    from origenlab_api.v2.commands import CommandRefused

    state = two_named_institutions
    repo = _repo(disposable_database)
    supplier = _attribute(
        repo, state, "supplier_record_id",
        {
            "organization_assertion_id": state["supplier_new_org_assertion_id"],
            "address_assertion_id": state["supplier_address_assertion_id"],
            "target": "new", "kind": "unknown", "name_display": None,
            "usage": "shared_mailbox",
        },
        key=f"move-first-{state['tag']}", digest="8" * 64,
    )
    _attribute(
        repo, state, "university_record_id",
        {
            "organization_assertion_id": state["university_existing_org_assertion_id"],
            "address_assertion_id": state["university_address_assertion_id"],
            "target": "existing",
            "organization_id": state["existing_organization_id"],
            "organization_version": state["existing_version"],
            "usage": "shared_mailbox",
        },
        key=f"move-second-{state['tag']}", digest="9" * 64,
    )

    # A third, fictional message asserting the university's address belongs to the supplier.
    import psycopg

    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "insert into evidence.source_record (kind, dedupe_key, payload) "
            "values ('gmail_message', %s, '{}'::jsonb) returning id::text",
            (f"pytest-attr:{state['tag']}:third",),
        )
        third_record = cur.fetchone()[0]
        cur.execute(
            "insert into evidence.assertion (source_record_id, kind, value_norm, value, resolution) "
            "values (%s, 'organization_name', %s, %s::jsonb, 'unresolved') returning id::text",
            (third_record, state["new_name"].lower(),
             f'{{"observed_value": "{state["new_name"]}"}}'),
        )
        third_org_assertion = cur.fetchone()[0]
        cur.execute(
            "insert into evidence.assertion (source_record_id, kind, value_norm, value, resolution) "
            "values (%s, 'contact_address', %s, %s::jsonb, 'unresolved') returning id::text",
            (third_record, state["university_address"],
             f'{{"observed_value": "{state["university_address"]}"}}'),
        )
        third_address_assertion = cur.fetchone()[0]

    state["third_record_id"] = third_record
    with pytest.raises(CommandRefused) as exc:
        _attribute(
            repo, state, "third_record_id",
            {
                "organization_assertion_id": third_org_assertion,
                "address_assertion_id": third_address_assertion,
                "target": "existing",
                "organization_id": supplier["organization_id"],
                "organization_version": 1,
                "usage": "shared_mailbox",
            },
            key=f"move-third-{state['tag']}", digest="a" * 64,
        )
    assert exc.value.code == "contact_point_attached_elsewhere"


@_needs_db
def test_a_refused_attribution_leaves_no_half_decision_behind(
    disposable_database, two_named_institutions
) -> None:
    """The atomicity claim, proven by making the second half fail.

    The address is attached to somebody else first, so the attach refuses. The organization
    the same command would have created must not exist afterwards — and neither must the
    receipt, so the key stays free for the corrected request.
    """
    import psycopg

    from origenlab_api.v2.commands import CommandRefused

    state = two_named_institutions
    repo = _repo(disposable_database)

    # The university takes its own mailbox first.
    _attribute(
        repo, state, "university_record_id",
        {
            "organization_assertion_id": state["university_existing_org_assertion_id"],
            "address_assertion_id": state["university_address_assertion_id"],
            "target": "existing",
            "organization_id": state["existing_organization_id"],
            "organization_version": state["existing_version"],
            "usage": "shared_mailbox",
        },
        key=f"half-first-{state['tag']}", digest="b" * 64,
    )

    # A new message asserting the new institution AND that same, already-taken address.
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            "insert into evidence.source_record (kind, dedupe_key, payload) "
            "values ('gmail_message', %s, '{}'::jsonb) returning id::text",
            (f"pytest-attr:{state['tag']}:half",),
        )
        half_record = cur.fetchone()[0]
        cur.execute(
            "insert into evidence.assertion (source_record_id, kind, value_norm, value, resolution) "
            "values (%s, 'organization_name', %s, %s::jsonb, 'unresolved') returning id::text",
            (half_record, state["new_name"].lower(),
             f'{{"observed_value": "{state["new_name"]}"}}'),
        )
        half_org_assertion = cur.fetchone()[0]
        cur.execute(
            "insert into evidence.assertion (source_record_id, kind, value_norm, value, resolution) "
            "values (%s, 'contact_address', %s, %s::jsonb, 'unresolved') returning id::text",
            (half_record, state["university_address"],
             f'{{"observed_value": "{state["university_address"]}"}}'),
        )
        half_address_assertion = cur.fetchone()[0]

    state["half_record_id"] = half_record
    key = f"half-second-{state['tag']}"
    with pytest.raises(CommandRefused) as exc:
        _attribute(
            repo, state, "half_record_id",
            {
                "organization_assertion_id": half_org_assertion,
                "address_assertion_id": half_address_assertion,
                "target": "new", "kind": "unknown", "name_display": None,
                "usage": "shared_mailbox",
            },
            key=key, digest="c" * 64,
        )
    assert exc.value.code == "contact_point_attached_elsewhere"

    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        # The organization the refused command would have created does not exist.
        cur.execute(
            "select count(*) from crm.organization where lower(name) = %s",
            (state["new_name"].lower(),),
        )
        assert cur.fetchone()[0] == 0
        # Neither assertion of that message moved.
        cur.execute(
            "select count(*) from evidence.assertion where source_record_id = %s "
            "and resolution <> 'unresolved'",
            (half_record,),
        )
        assert cur.fetchone()[0] == 0
        # And the key is free: the receipt rolled back with everything else.
        cur.execute(
            "select count(*) from platform.command_receipt where idempotency_key = %s", (key,)
        )
        assert cur.fetchone()[0] == 0


@_needs_db
def test_an_attribution_replays_instead_of_deciding_twice(
    disposable_database, one_unknown_institution
) -> None:
    """One key, one decision — the second call answers from the receipt."""
    import psycopg

    state = one_unknown_institution
    repo = _repo(disposable_database)
    fields = {
        "organization_assertion_id": state["org_assertion_id"],
        "address_assertion_id": state["address_assertion_id"],
        "target": "new", "kind": "unknown", "name_display": None,
        "usage": "shared_mailbox",
    }
    key = f"replay-{state['tag']}"
    first = _attribute(repo, state, "source_record_id", fields, key=key, digest="d" * 64)
    second = _attribute(repo, state, "source_record_id", fields, key=key, digest="d" * 64)

    assert first["replayed"] is False
    assert second["replayed"] is True
    assert second["organization_id"] == first["organization_id"]
    assert second["contact_point_id"] == first["contact_point_id"]

    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute(
            "select count(*) from crm.organization where lower(name) = %s",
            (state["org_name"].lower(),),
        )
        assert cur.fetchone()[0] == 1


@_needs_db
def test_an_attribution_will_not_take_an_institution_the_message_does_not_name(
    disposable_database, two_named_institutions, one_unknown_institution
) -> None:
    """Exact name or nothing, in the composed command exactly as in the single one."""
    from origenlab_api.v2.commands import CommandRefused

    state = two_named_institutions
    with pytest.raises(CommandRefused) as exc:
        _attribute(
            _repo(disposable_database), state, "supplier_record_id",
            {
                # The assertion names the *new* institution; the id points at the existing one.
                "organization_assertion_id": state["supplier_new_org_assertion_id"],
                "address_assertion_id": state["supplier_address_assertion_id"],
                "target": "existing",
                "organization_id": state["existing_organization_id"],
                "organization_version": state["existing_version"],
                "usage": "shared_mailbox",
            },
            key=f"mismatch-{state['tag']}", digest="e" * 64,
        )
    assert exc.value.code == "organization_name_is_not_an_exact_match"


@_needs_db
def test_an_attribution_refuses_an_assertion_from_another_message(
    disposable_database, two_named_institutions, one_unknown_institution
) -> None:
    """Pairing two unrelated ids would record a decision about a message nobody read."""
    from origenlab_api.v2.commands import CommandRefused

    state = two_named_institutions
    with pytest.raises(CommandRefused) as exc:
        _attribute(
            _repo(disposable_database), state, "supplier_record_id",
            {
                "organization_assertion_id": state["supplier_new_org_assertion_id"],
                "address_assertion_id": one_unknown_institution["address_assertion_id"],
                "target": "new", "kind": "unknown", "name_display": None,
                "usage": "shared_mailbox",
            },
            key=f"foreign-{state['tag']}", digest="f" * 64,
        )
    assert exc.value.code == "assertion_belongs_to_another_record"


# ------------------------------------------- the relationship an address has to an institution
#
# `shared_mailbox` was once the only `usage` the schema could hold for an address attached to
# an organization with no person, so it was what every attribution wrote — including on
# `jperez@`, where it asserts that a named individual's mailbox is a desk several people read.
# Nobody decided that. These tests are about the two things that replace it: a truthful
# neutral value, and a refusal to make the shared-mailbox claim by accident.


def _attach(**overrides) -> AttachContactAddressBody:
    body = {
        "source_record_id": RECORD_ID,
        "note": "es el buzón de esta institución",
        "assertion_id": ASSERTION_ID,
        "organization_id": ORGANIZATION_ID,
        "usage": "individual_owner_unknown",
    }
    body.update(overrides)
    return AttachContactAddressBody(**body)


def test_the_two_reachable_relationships_are_the_vocabulary() -> None:
    """One says a desk is shared; the other says an individual owns it and nobody knows who."""
    assert set(ATTACHABLE_USAGE) == {"shared_mailbox", "individual_owner_unknown"}


@pytest.mark.parametrize(
    "path, body",
    [
        (
            "/v2/commands/attach-contact-address",
            {"assertion_id": ASSERTION_ID, "organization_id": ORGANIZATION_ID},
        ),
        (
            "/v2/commands/attribute-sender-organization",
            {
                "organization_assertion_id": ASSERTION_ID,
                "address_assertion_id": ADDRESS_ASSERTION_ID,
                "target": "new",
            },
        ),
    ],
)
def test_neither_command_has_a_default_relationship(path: str, body: dict) -> None:
    """A request that does not say what the address is to the institution is not a decision."""
    repo = _FakeCommandRepo()
    response = _client(repo).post(path, json=_body(**body), headers=_headers())
    assert response.status_code == 422
    assert repo.calls == []


@pytest.mark.parametrize(
    "address, is_a_desk",
    [
        ("ventas@proveedor.example", True),
        ("secretaria@universidad.example", True),
        ("produccion@planta.example", True),
        ("contacto+equipos@proveedor.example", True),
        ("ventas.sur@proveedor.example", True),
        ("jperez@universidad.example", False),
        ("p.morales@universidad.example", False),
        ("pmorales@universidad.example", False),
        ("j.perez-soto@universidad.example", False),
        ("maria.gonzalez@corteva.com", False),
    ],
)
def test_only_a_recognised_role_local_part_reads_as_a_desk(address: str, is_a_desk: bool) -> None:
    """Unknown stays unknown: an unrecognised local part is not evidence of a desk."""
    assert address_names_a_desk(address) is is_a_desk


def test_a_named_address_may_not_be_called_a_shared_mailbox() -> None:
    with pytest.raises(CommandRefused) as exc:
        require_override_for_a_named_address(
            value_norm="jperez@universidad.example",
            usage="shared_mailbox",
            override_note=None,
        )
    assert exc.value.code == "named_address_is_not_a_shared_mailbox_by_default"
    assert exc.value.status_code == 422
    # The refusal names the alternative, because an operator who is refused and not told what
    # to do instead will reach for whatever the form still accepts.
    assert "individual_owner_unknown" in exc.value.message


def test_a_named_address_may_be_called_a_shared_mailbox_with_an_explicit_override() -> None:
    """The claim stays available; it stops being free."""
    require_override_for_a_named_address(
        value_norm="jperez@universidad.example",
        usage="shared_mailbox",
        override_note="la secretaria y el jefe de laboratorio responden desde esta casilla",
    )


def test_a_blank_override_is_not_an_override() -> None:
    with pytest.raises(CommandRefused) as exc:
        require_override_for_a_named_address(
            value_norm="jperez@universidad.example", usage="shared_mailbox", override_note="   "
        )
    assert exc.value.code == "named_address_is_not_a_shared_mailbox_by_default"


def test_a_role_address_needs_no_override_and_a_neutral_one_never_does() -> None:
    require_override_for_a_named_address(
        value_norm="ventas@proveedor.example", usage="shared_mailbox", override_note=None
    )
    require_override_for_a_named_address(
        value_norm="jperez@universidad.example",
        usage="individual_owner_unknown",
        override_note=None,
    )


def test_an_override_note_belongs_only_to_the_claim_it_justifies() -> None:
    """A justification for a claim the request does not make is refused, not dropped."""
    with pytest.raises(CommandRefused) as exc:
        validated(
            ATTACH_CONTACT_ADDRESS,
            _attach(
                usage="individual_owner_unknown",
                shared_mailbox_override_note="lo leen varias personas",
            ),
        )
    assert exc.value.code == "override_note_is_only_for_shared_mailbox"

    with pytest.raises(CommandRefused) as exc:
        validated(
            ATTRIBUTE_SENDER_ORGANIZATION,
            _attribution(
                usage="individual_owner_unknown",
                shared_mailbox_override_note="lo leen varias personas",
            ),
        )
    assert exc.value.code == "override_note_is_only_for_shared_mailbox"


def test_the_neutral_relationship_validates_on_both_commands() -> None:
    attach = validated(ATTACH_CONTACT_ADDRESS, _attach())
    assert attach["usage"] == "individual_owner_unknown"
    assert attach["shared_mailbox_override_note"] is None

    attribute = validated(
        ATTRIBUTE_SENDER_ORGANIZATION, _attribution(usage="individual_owner_unknown")
    )
    assert attribute["usage"] == "individual_owner_unknown"
    assert attribute["shared_mailbox_override_note"] is None


def test_a_space_is_not_a_justification() -> None:
    """So the transaction's refusal cannot be dodged by sending a blank note."""
    repo = _FakeCommandRepo()
    response = _client(repo).post(
        "/v2/commands/attach-contact-address",
        json=_body(
            assertion_id=ASSERTION_ID,
            organization_id=ORGANIZATION_ID,
            usage="shared_mailbox",
            shared_mailbox_override_note="   ",
        ),
        headers=_headers(),
    )
    assert response.status_code == 422
    assert repo.calls == []


def test_the_relationship_changes_the_digest() -> None:
    """Two different claims about a person are two different requests under one key."""
    assert request_digest(ATTACH_CONTACT_ADDRESS, _attach(usage="shared_mailbox")) != (
        request_digest(ATTACH_CONTACT_ADDRESS, _attach(usage="individual_owner_unknown"))
    )


@pytest.mark.parametrize("usage", ["work", "personal", "unattributed", "owner_unknown"])
def test_a_relationship_outside_the_vocabulary_is_refused(usage: str) -> None:
    repo = _FakeCommandRepo()
    response = _client(repo).post(
        "/v2/commands/attach-contact-address",
        json=_body(assertion_id=ASSERTION_ID, organization_id=ORGANIZATION_ID, usage=usage),
        headers=_headers(),
    )
    assert response.status_code == 422, usage
    assert repo.calls == []


# ------------------------------- the relationship, proved against the schema that stores it
#
# The unit tests above prove the refusal. These prove the two things only a database can: that
# `individual_owner_unknown` is a shape `crm.contact_point` actually accepts, and that a row
# written under it records the institution and no person at all.


@pytest.fixture
def named_sender(disposable_database, seeded):
    """The same record, plus a second address assertion that looks like a person's."""
    import psycopg

    address = f"jperez-{seeded['tag']}@universidad.example"
    with psycopg.connect(disposable_database, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")
        cur.execute(
            """
            insert into evidence.assertion
                (source_record_id, kind, value_norm, value, resolution)
            values (%s, 'contact_address', %s, %s::jsonb, 'unresolved')
            returning id::text
            """,
            (
                seeded["source_record_id"],
                address,
                f'{{"observed_value": "{address}"}}',
            ),
        )
        return {**seeded, "named_address": address, "named_assertion_id": cur.fetchone()[0]}


def _attach_named(repo, state, organization_id, fields, key, digest):
    return _run(
        repo,
        ATTACH_CONTACT_ADDRESS,
        state,
        {
            "assertion_id": state["named_assertion_id"],
            "organization_id": organization_id,
            **fields,
        },
        key=key,
        digest=digest,
    )


def _an_organization(repo, state):
    return _run(
        repo,
        CREATE_ORGANIZATION,
        state,
        {
            "assertion_id": state["organization_name_assertion_id"],
            "kind": "unknown",
            "name_display": None,
        },
        key=f"org-{state['tag']}",
        digest="1" * 64,
    )["organization_id"]


@_needs_db
def test_a_named_address_is_refused_as_a_shared_mailbox_and_writes_nothing(
    disposable_database, named_sender
) -> None:
    """The refusal this whole change exists for, at the boundary that owns the write."""
    import psycopg

    from origenlab_api.v2.commands import CommandRefused

    repo = _repo(disposable_database)
    organization_id = _an_organization(repo, named_sender)

    with pytest.raises(CommandRefused) as exc:
        _attach_named(
            repo,
            named_sender,
            organization_id,
            {"usage": "shared_mailbox", "shared_mailbox_override_note": None},
            key=f"refused-{named_sender['tag']}",
            digest="2" * 64,
        )
    assert exc.value.code == "named_address_is_not_a_shared_mailbox_by_default"

    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute(
            "select count(*) from crm.contact_point where value_norm = %s",
            (named_sender["named_address"],),
        )
        assert cur.fetchone()[0] == 0
        cur.execute(
            "select resolution from evidence.assertion where id = %s",
            (named_sender["named_assertion_id"],),
        )
        assert cur.fetchone()[0] == "unresolved"
        # A refused command leaves its key free: the operator fixes the request and retries.
        cur.execute(
            "select count(*) from platform.command_receipt where idempotency_key = %s",
            (f"refused-{named_sender['tag']}",),
        )
        assert cur.fetchone()[0] == 0


@_needs_db
def test_the_neutral_relationship_records_the_institution_and_no_person(
    disposable_database, named_sender
) -> None:
    import psycopg

    repo = _repo(disposable_database)
    organization_id = _an_organization(repo, named_sender)
    result = _attach_named(
        repo,
        named_sender,
        organization_id,
        {"usage": "individual_owner_unknown", "shared_mailbox_override_note": None},
        key=f"neutral-{named_sender['tag']}",
        digest="3" * 64,
    )
    assert result["created"] == ["contact_point"]

    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute(
            "select usage, person_id, organization_id::text, confirmation "
            "from crm.contact_point where value_norm = %s",
            (named_sender["named_address"],),
        )
        assert cur.fetchone() == (
            "individual_owner_unknown", None, organization_id, "confirmed",
        )
        # It named nobody. That is the claim it declines to make, so it is worth checking
        # rather than trusting the absence of an INSERT in the code.
        cur.execute(
            "select count(*) from crm.person where origin_source_record_id = %s",
            (named_sender["source_record_id"],),
        )
        assert cur.fetchone()[0] == 0
        cur.execute("select count(*) from crm.affiliation")
        assert cur.fetchone()[0] == 0
        cur.execute(
            "select payload->>'usage', payload->>'shared_mailbox_override_note' "
            "from crm.domain_event where event_type = 'contact_point.created' "
            "and aggregate_id = %s",
            (result["contact_point_id"],),
        )
        assert cur.fetchone() == ("individual_owner_unknown", None)


@_needs_db
def test_the_shared_mailbox_claim_survives_with_a_note_and_the_note_is_kept(
    disposable_database, named_sender
) -> None:
    """The operator who knows better is not blocked — they are recorded."""
    import psycopg

    repo = _repo(disposable_database)
    organization_id = _an_organization(repo, named_sender)
    justification = "la secretaria y el jefe de laboratorio responden desde esta casilla"
    result = _attach_named(
        repo,
        named_sender,
        organization_id,
        {"usage": "shared_mailbox", "shared_mailbox_override_note": justification},
        key=f"override-{named_sender['tag']}",
        digest="4" * 64,
    )
    assert result["created"] == ["contact_point"]

    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute(
            "select usage, person_id from crm.contact_point where value_norm = %s",
            (named_sender["named_address"],),
        )
        assert cur.fetchone() == ("shared_mailbox", None)
        cur.execute(
            "select payload->>'shared_mailbox_override_note' from crm.domain_event "
            "where event_type = 'contact_point.created' and aggregate_id = %s",
            (result["contact_point_id"],),
        )
        assert cur.fetchone()[0] == justification


@_needs_db
def test_a_role_address_still_needs_no_override(disposable_database, seeded) -> None:
    """`contacto-<tag>@` is a desk under a suffix, and the rule has nothing to say about it."""
    repo = _repo(disposable_database)
    organization_id = _an_organization(repo, seeded)
    result = _run(
        repo,
        ATTACH_CONTACT_ADDRESS,
        seeded,
        {
            "assertion_id": seeded["contact_address_assertion_id"],
            "organization_id": organization_id,
            "usage": "shared_mailbox",
            "shared_mailbox_override_note": None,
        },
        key=f"role-{seeded['tag']}",
        digest="5" * 64,
    )
    assert result["created"] == ["contact_point"]
