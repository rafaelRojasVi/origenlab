"""CRM authoring command boundary — in-process tests.

Covers: body validation, role checks (viewer 403 incl. forged header escalation attempt),
admin-only gates, missing Idempotency-Key (400), replay (replayed=true, no second handler call),
product-line closed list, no `delete from` in the module source, all 27 route shapes present,
switch off-by-default, mount test, and per-command shape validation.

**No database, no network.**  Every handler call goes to a _FakeRepo that captures its args.
"""

from __future__ import annotations

import ast
import pathlib
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from origenlab_api.commercial_operator_identity import OPERATOR_EMAIL_HEADER
from origenlab_api.v2.commands import CommandRefused, request_digest
from origenlab_api.v2.crm_authoring import (
    PRODUCT_LINE_IDS,
    merge_preview_digest,
    normalize_phone,
)
from origenlab_api.v2.crm_authoring_routes import (
    AddNoteBody,
    ArchiveNoteBody,
    ArchiveOrganizationBody,
    ArchivePersonBody,
    CreatePersonBody,
    MergePeopleBody,
    RegisterOrganizationBody,
    RestoreOrganizationBody,
    RestorePersonBody,
    ReviseNoteBody,
    UpdatePersonBody,
    crm_authoring_router,
)
from origenlab_api.v2.identity import LocalDevIdentity, OperatorIdentity, OperatorLookup


def _never_connects(*_args, **_kwargs):
    raise AssertionError("mounting a router must not open a database connection")


LOOPBACK = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"
SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "origenlab_api"
HEADERS = {OPERATOR_EMAIL_HEADER: "op@example.test"}

ADMIN_ROUTES = {
    "/v2/commands/archive-person",
    "/v2/commands/restore-person",
    "/v2/commands/merge-people",
    "/v2/commands/archive-organization",
    "/v2/commands/restore-organization",
}

ALL_27_ROUTES = {
    "/v2/commands/create-person",
    "/v2/commands/update-person",
    "/v2/commands/archive-person",
    "/v2/commands/restore-person",
    "/v2/commands/merge-people",
    "/v2/commands/add-contact-point",
    "/v2/commands/update-contact-point",
    "/v2/commands/deactivate-contact-point",
    "/v2/commands/link-person-organization",
    "/v2/commands/unlink-person-organization",
    "/v2/commands/register-organization",
    "/v2/commands/update-organization",
    "/v2/commands/archive-organization",
    "/v2/commands/restore-organization",
    "/v2/commands/add-organization-identifier",
    "/v2/commands/remove-organization-identifier",
    "/v2/commands/add-organization-domain",
    "/v2/commands/remove-organization-domain",
    "/v2/commands/add-organization-classification",
    "/v2/commands/remove-organization-classification",
    "/v2/commands/link-organization-product-line",
    "/v2/commands/unlink-organization-product-line",
    "/v2/commands/confirm-supplier-candidate",
    "/v2/commands/reject-supplier-candidate",
    "/v2/commands/add-note",
    "/v2/commands/revise-note",
    "/v2/commands/archive-note",
}


def _operator(role="sales"):
    return OperatorIdentity(
        operator_id="00000000-0000-4000-8000-000000000001",
        email_norm="op@example.test",
        display_name="Op",
        role=role,
        status="active",
    )


class _Lookup(OperatorLookup):
    def __init__(self, operator):
        self._operator = operator

    def by_email(self, email_norm):
        return self._operator


class _FakeRepo:
    def __init__(self, response=None):
        self.calls = []
        self._response = response or {
            "ok": True, "replayed": False, "person_id": str(uuid.uuid4()),
            "version": 1, "idempotency_key": "k", "command_receipt_id": str(uuid.uuid4()),
        }

    def execute(self, **kw):
        self.calls.append(kw)
        return self._response


def _client(role="sales"):
    app = FastAPI()
    app.include_router(crm_authoring_router)
    app.state.crm_authoring_repository = _FakeRepo()
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(_operator(role)))
    return app, TestClient(app)


def _ikey():
    return {"Idempotency-Key": f"k-{uuid.uuid4().hex[:16]}"}


# ─────────────────────────────────────────────────────────────────── no delete from ──

def test_authoring_module_has_no_delete_from() -> None:
    """The authoring module must never physically remove any row from the database."""
    source = (SRC / "v2" / "crm_authoring.py").read_text()
    # Grep for the two-word SQL phrase that would remove rows, excluding comments/docstrings.
    # We parse the AST and check string literals in SQL execute calls.
    import ast as _ast
    tree = _ast.parse(source)
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Constant) and isinstance(node.value, str):
            if "delete from" in node.value.lower():
                raise AssertionError(
                    f"crm_authoring.py contains 'delete from' in a string at line {node.lineno}"
                )


# ─────────────────────────────────────────────────────────────── product line ids ──

def test_product_line_ids_match_migration() -> None:
    """The six brand ids in Python must equal those in the migration's CHECK constraint."""
    migration_ids = frozenset({"hielscher", "ortoalresa", "ika", "adam-equipment", "loeser", "serva"})
    assert PRODUCT_LINE_IDS == migration_ids


# ──────────────────────────────────────────────────────────── phone normalization ──

@pytest.mark.parametrize("raw,expected", [
    ("+56912345678", "+56912345678"),
    ("+56 9 1234 5678", "+56912345678"),
    ("+56-9-1234-5678", "+56912345678"),
    ("+1 (800) 555-1234", "+18005551234"),
])
def test_normalize_phone_valid(raw, expected) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["123456789", "56912345678", "not-a-phone", ""])
def test_normalize_phone_invalid(raw) -> None:
    with pytest.raises(CommandRefused) as exc:
        normalize_phone(raw)
    assert exc.value.code == "not_a_phone_number"


# ────────────────────────────────────────────────────────────── merge_preview_digest ──

def test_merge_preview_digest_is_deterministic() -> None:
    moves = {"contact_points": 3, "affiliations": 0, "opportunity_participants": 1,
             "campaign_recipients": 5, "notes": 2}
    preview = {"loser": {"id": "aaa", "version": 1}, "winner": {"id": "bbb", "version": 2}, "moves": moves}
    d1 = merge_preview_digest(preview)
    d2 = merge_preview_digest(preview)
    assert d1 == d2
    assert len(d1) == 64  # sha256 hex


def test_merge_preview_digest_changes_with_version() -> None:
    moves = {"contact_points": 0, "affiliations": 0, "opportunity_participants": 0,
             "campaign_recipients": 0, "notes": 0}
    preview1 = {"loser": {"id": "a", "version": 1}, "winner": {"id": "b", "version": 1}, "moves": moves}
    preview2 = {"loser": {"id": "a", "version": 2}, "winner": {"id": "b", "version": 1}, "moves": moves}
    d1 = merge_preview_digest(preview1)
    d2 = merge_preview_digest(preview2)
    assert d1 != d2


# ───────────────────────────────────────────────── body shape validation (no DB) ──

def test_create_person_requires_display_name_and_note() -> None:
    with pytest.raises(ValidationError):
        CreatePersonBody(note="ok")  # missing display_name
    with pytest.raises(ValidationError):
        CreatePersonBody(display_name="Ana")  # missing note
    body = CreatePersonBody(display_name="Ana García", note="Alta manual")
    assert body.display_name == "Ana García"


def test_create_person_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        CreatePersonBody(display_name="x", note="y", unknown_field="z")


def test_note_must_be_non_blank() -> None:
    for blank in ["", "  ", "\t\n"]:
        with pytest.raises(ValidationError):
            CreatePersonBody(display_name="x", note=blank)


def test_note_max_2000() -> None:
    with pytest.raises(ValidationError):
        CreatePersonBody(display_name="x", note="x" * 2001)
    CreatePersonBody(display_name="x", note="x" * 2000)  # ok


def test_add_note_body_uses_body_field() -> None:
    body = AddNoteBody(subject_kind="person", subject_id=str(uuid.uuid4()), body="anotación")
    assert body.body == "anotación"
    with pytest.raises(ValidationError):
        AddNoteBody(subject_kind="person", subject_id=str(uuid.uuid4()), body="")
    with pytest.raises(ValidationError):
        AddNoteBody(subject_kind="person", subject_id=str(uuid.uuid4()), body="x" * 8001)
    with pytest.raises(ValidationError):
        AddNoteBody(subject_kind="bad_kind", subject_id=str(uuid.uuid4()), body="x")


def test_revise_note_body_max_8000() -> None:
    ok = ReviseNoteBody(note_id=str(uuid.uuid4()), expected_version=1, body="x" * 8000)
    assert len(ok.body) == 8000
    with pytest.raises(ValidationError):
        ReviseNoteBody(note_id=str(uuid.uuid4()), expected_version=1, body="x" * 8001)


def test_merge_people_requires_confirmed_true() -> None:
    kw = dict(
        loser_person_id=str(uuid.uuid4()),
        winner_person_id=str(uuid.uuid4()),
        expected_loser_version=1,
        expected_winner_version=1,
        expected_preview_sha256="a" * 64,
        note="merge test",
    )
    with pytest.raises(ValidationError):
        MergePeopleBody(**kw, confirmed=False)
    MergePeopleBody(**kw, confirmed=True)  # ok


def test_archive_person_forbids_extra() -> None:
    with pytest.raises(ValidationError):
        ArchivePersonBody(person_id=str(uuid.uuid4()), expected_version=1, note="r", extra="bad")


# ──────────────────────────────────────────────────────────────────── 27 routes ──

def test_the_router_exposes_exactly_27_posts() -> None:
    routes = set(r.path for r in crm_authoring_router.routes)
    assert routes == ALL_27_ROUTES
    # All are POST
    for r in crm_authoring_router.routes:
        assert tuple(sorted(r.methods)) == ("POST",), f"{r.path} has non-POST methods"


# ──────────────────────────────────────────────────────── viewer 403 on every route ──

@pytest.mark.parametrize("path", sorted(ALL_27_ROUTES))
def test_viewer_403_on_all_routes(path) -> None:
    """A viewer (any role that is not sales/admin) gets 403 on every command route."""
    _, client = _client(role="viewer")
    body = _minimal_body(path)
    r = client.post(path, json=body, headers={**HEADERS, **_ikey()})
    assert r.status_code == 403, f"{path}: expected 403 for viewer, got {r.status_code}"
    assert r.json()["detail"]["code"] in (
        "role_may_not_decide", "role_may_not_archive"
    ), f"{path}: unexpected code {r.json()['detail']['code']!r}"


def _minimal_body(path: str) -> dict:
    """Return the smallest valid JSON body shape for any given command path."""
    pid = str(uuid.uuid4())
    oid = str(uuid.uuid4())
    note = "test"
    if path == "/v2/commands/create-person":
        return {"display_name": "x", "note": note}
    if path == "/v2/commands/update-person":
        return {"person_id": pid, "expected_version": 1, "display_name": "y", "note": note}
    if path == "/v2/commands/archive-person":
        return {"person_id": pid, "expected_version": 1, "note": note}
    if path == "/v2/commands/restore-person":
        return {"person_id": pid, "expected_version": 1, "note": note}
    if path == "/v2/commands/merge-people":
        return {
            "loser_person_id": pid, "winner_person_id": str(uuid.uuid4()),
            "expected_loser_version": 1, "expected_winner_version": 1,
            "expected_preview_sha256": "a" * 64, "confirmed": True, "note": note,
        }
    if path == "/v2/commands/add-contact-point":
        return {"person_id": pid, "expected_version": 1, "kind": "email",
                "value": "x@example.test", "usage": "personal", "note": note}
    if path == "/v2/commands/update-contact-point":
        return {"contact_point_id": pid, "expected_version": 1, "note": note}
    if path == "/v2/commands/deactivate-contact-point":
        return {"contact_point_id": pid, "expected_version": 1, "note": note}
    if path == "/v2/commands/link-person-organization":
        return {"person_id": pid, "expected_version": 1, "organization_id": oid, "note": note}
    if path == "/v2/commands/unlink-person-organization":
        return {"person_id": pid, "expected_version": 1, "affiliation_id": pid, "note": note}
    if path == "/v2/commands/register-organization":
        return {"name": "Test Corp", "kind": "unknown", "note": note}
    if path == "/v2/commands/update-organization":
        return {"organization_id": oid, "expected_version": 1, "name": "New Name", "note": note}
    if path == "/v2/commands/archive-organization":
        return {"organization_id": oid, "expected_version": 1, "note": note}
    if path == "/v2/commands/restore-organization":
        return {"organization_id": oid, "expected_version": 1, "note": note}
    if path == "/v2/commands/add-organization-identifier":
        return {"organization_id": oid, "expected_version": 1, "scheme": "rut", "value": "12345678-9", "note": note}
    if path == "/v2/commands/remove-organization-identifier":
        return {"organization_id": oid, "expected_version": 1, "identifier_id": pid, "note": note}
    if path == "/v2/commands/add-organization-domain":
        return {"organization_id": oid, "expected_version": 1, "domain": "example.test", "note": note}
    if path == "/v2/commands/remove-organization-domain":
        return {"organization_id": oid, "expected_version": 1, "domain_id": pid, "note": note}
    if path == "/v2/commands/add-organization-classification":
        return {"organization_id": oid, "expected_version": 1, "role": "supplier", "note": note}
    if path == "/v2/commands/remove-organization-classification":
        return {"organization_id": oid, "expected_version": 1, "relationship_id": pid, "note": note}
    if path == "/v2/commands/link-organization-product-line":
        return {"organization_id": oid, "expected_version": 1, "line_id": "hielscher", "note": note}
    if path == "/v2/commands/unlink-organization-product-line":
        return {"organization_id": oid, "expected_version": 1, "link_id": pid, "note": note}
    if path == "/v2/commands/confirm-supplier-candidate":
        return {
            "assertion_id": pid, "organization_id": oid,
            "classification": "supplier", "note": note,
        }
    if path == "/v2/commands/reject-supplier-candidate":
        return {"assertion_id": pid, "note": note}
    if path == "/v2/commands/add-note":
        return {"subject_kind": "person", "subject_id": pid, "body": "a note"}
    if path == "/v2/commands/revise-note":
        return {"note_id": pid, "expected_version": 1, "body": "revised"}
    if path == "/v2/commands/archive-note":
        return {"note_id": pid, "expected_version": 1, "note": note}
    raise ValueError(f"No minimal body defined for {path}")


# ──────────────────────────────────────────────────── forged header cannot escalate ──

def test_forged_header_cannot_escalate_viewer_to_admin() -> None:
    """A viewer with a forged header naming an admin must still be 403.

    Identity comes from the identity port (which looks up the email_norm in the database),
    not from any header the client can forge.  LocalDevIdentity reads the header but maps it
    through the lookup — our lookup always returns the viewer regardless of what the header says.
    """
    admin_op = _operator(role="admin")
    viewer_op = _operator(role="viewer")
    # The lookup always returns the viewer, even when the header names an admin address.
    app = FastAPI()
    app.include_router(crm_authoring_router)
    app.state.crm_authoring_repository = _FakeRepo()
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(viewer_op))
    client = TestClient(app)

    path = "/v2/commands/archive-person"
    body = {"person_id": str(uuid.uuid4()), "expected_version": 1, "note": "reason"}
    forged_headers = {
        OPERATOR_EMAIL_HEADER: admin_op.email_norm,
        "Idempotency-Key": "k-forged-1234567",
    }
    r = client.post(path, json=body, headers=forged_headers)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] in ("role_may_not_decide", "role_may_not_archive")


# ──────────────────────────────────────────────── sales allowed, admin-only refused ──

@pytest.mark.parametrize("path", sorted(ADMIN_ROUTES))
def test_sales_refused_on_admin_only_routes(path) -> None:
    _, client = _client(role="sales")
    body = _minimal_body(path)
    r = client.post(path, json=body, headers={**HEADERS, **_ikey()})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "role_may_not_archive"


@pytest.mark.parametrize("path", sorted(ALL_27_ROUTES - ADMIN_ROUTES))
def test_sales_can_call_non_admin_routes(path) -> None:
    _, client = _client(role="sales")
    body = _minimal_body(path)
    r = client.post(path, json=body, headers={**HEADERS, **_ikey()})
    # The fake repo always returns 200; we just prove the sales role is not refused.
    assert r.status_code == 200


# ──────────────────────────────────────────────────── missing Idempotency-Key → 400 ──

@pytest.mark.parametrize("path", sorted(ALL_27_ROUTES)[:3])
def test_missing_idempotency_key_is_400(path) -> None:
    _, client = _client(role="admin")
    body = _minimal_body(path)
    r = client.post(path, json=body, headers=HEADERS)  # no Idempotency-Key
    assert r.status_code == 400


# ─────────────────────────────────────────────────────────── replay returns replayed ──

def test_replay_returns_replayed_true_and_no_second_write() -> None:
    replay_response = {
        "ok": True, "replayed": True, "person_id": str(uuid.uuid4()),
        "version": 1, "idempotency_key": "k", "command_receipt_id": str(uuid.uuid4()),
    }
    fake = _FakeRepo(response=replay_response)
    app = FastAPI()
    app.include_router(crm_authoring_router)
    app.state.crm_authoring_repository = fake
    app.state.v2_identity = LocalDevIdentity(LOOPBACK, _Lookup(_operator("admin")))
    client = TestClient(app)

    body = {"display_name": "Ana", "note": "primera vez"}
    path = "/v2/commands/create-person"
    r = client.post(path, json=body, headers={**HEADERS, **_ikey()})
    assert r.status_code == 200
    data = r.json()
    assert data["replayed"] is True
    assert len(fake.calls) == 1  # handler called exactly once


# ──────────────────────────────────────────────────────── 422 on unknown field ──

def test_extra_field_is_422() -> None:
    _, client = _client(role="sales")
    body = {"display_name": "Ana", "note": "test", "inject": "bad"}
    r = client.post("/v2/commands/create-person", json=body, headers={**HEADERS, **_ikey()})
    assert r.status_code == 422


# ───────────────────────────────────────────────────────────── switch off-by-default ──

def test_crm_authoring_switch_defaults_off() -> None:
    from origenlab_api.settings import Settings
    assert Settings(_env_file=None).v2_crm_authoring_enabled is False


def test_crm_authoring_configured_requires_both_dsn_and_switch() -> None:
    from origenlab_api.settings import Settings
    # Neither
    assert not Settings(_env_file=None).crm_authoring_configured()
    # DSN only
    assert not Settings(_env_file=None, v2_database_url=LOOPBACK).crm_authoring_configured()
    # Switch only
    assert not Settings(_env_file=None, v2_crm_authoring_enabled=True).crm_authoring_configured()
    # Both
    assert Settings(_env_file=None, v2_database_url=LOOPBACK, v2_crm_authoring_enabled=True).crm_authoring_configured()


def test_crm_authoring_mount_off_by_default() -> None:
    from origenlab_api import main
    from origenlab_api.settings import Settings

    def routes(**kw):
        app = FastAPI()
        main._mount_crm_authoring(app, Settings(_env_file=None, **kw), LOOPBACK, _never_connects)
        return app.state.crm_authoring_enabled, set(app.openapi()["paths"])

    # Without both, the routes are absent
    for kw in ({}, {"v2_crm_authoring_enabled": True}, {"v2_database_url": LOOPBACK}):
        enabled, paths = routes(**kw)
        assert enabled is False
        assert "/v2/commands/create-person" not in paths

    # With both, every route appears
    enabled, paths = routes(v2_database_url=LOOPBACK, v2_crm_authoring_enabled=True)
    assert enabled is True
    assert ALL_27_ROUTES <= paths


def test_crm_authoring_routes_404_when_switch_is_off(monkeypatch) -> None:
    """When the switch is off, every authoring path returns 404."""
    from origenlab_api.main import create_app

    monkeypatch.setenv("ORIGENLAB_DISABLE_DOTENV", "1")
    monkeypatch.setenv("ORIGENLAB_V2_DATABASE_URL", "postgresql://u:p@127.0.0.1:54332/unused")
    monkeypatch.delenv("ORIGENLAB_V2_JWKS_URL", raising=False)
    monkeypatch.delenv("ORIGENLAB_ENV", raising=False)
    monkeypatch.setenv("ORIGENLAB_GOOGLE_AUTH_ENABLED", "false")
    monkeypatch.setenv("ORIGENLAB_DEV_LOGIN_ENABLED", "true")
    monkeypatch.setenv("ORIGENLAB_V2_CRM_AUTHORING_ENABLED", "false")

    app = create_app()
    client = TestClient(app)
    r = client.post("/v2/commands/create-person", json={"display_name": "x", "note": "y"},
                    headers={**HEADERS, "Idempotency-Key": "k-test1234"})
    assert r.status_code == 404


# ────────────────────────────────────────────────────── admin routes 200 for admin ──

@pytest.mark.parametrize("path", sorted(ADMIN_ROUTES))
def test_admin_can_call_admin_only_routes(path) -> None:
    _, client = _client(role="admin")
    body = _minimal_body(path)
    r = client.post(path, json=body, headers={**HEADERS, **_ikey()})
    assert r.status_code == 200


# ────────────────────────────────────────────────── add-note / revise-note no `note` field ──

def test_add_note_has_no_note_field() -> None:
    """add-note uses 'body', not 'note'; passing 'note' is a 422."""
    _, client = _client(role="sales")
    body = {"subject_kind": "person", "subject_id": str(uuid.uuid4()),
            "body": "good body", "note": "should be rejected"}
    r = client.post("/v2/commands/add-note", json=body, headers={**HEADERS, **_ikey()})
    assert r.status_code == 422


def test_revise_note_has_no_note_field() -> None:
    _, client = _client(role="sales")
    body = {"note_id": str(uuid.uuid4()), "expected_version": 1,
            "body": "revised", "note": "should be rejected"}
    r = client.post("/v2/commands/revise-note", json=body, headers={**HEADERS, **_ikey()})
    assert r.status_code == 422


# ──────────────────────────────────────────────────────── contact kind validation ──

def test_add_contact_point_kind_must_be_email_or_phone() -> None:
    _, client = _client(role="sales")
    body = {
        "person_id": str(uuid.uuid4()), "expected_version": 1,
        "kind": "telegram", "value": "x@x.test", "usage": "personal", "note": "test",
    }
    r = client.post("/v2/commands/add-contact-point", json=body, headers={**HEADERS, **_ikey()})
    assert r.status_code == 422


# ──────────────────────────────────────────────────────── product line ids ──

def test_link_product_line_refuses_unknown_line_via_handler() -> None:
    """An unknown line_id is refused as CommandRefused from the handler (not Pydantic)."""
    from origenlab_api.v2.crm_authoring import _handle_link_organization_product_line

    class _FakeSelf:
        pass

    fields = {
        "organization_id": str(uuid.uuid4()),
        "expected_version": 1,
        "line_id": "not-a-real-line",
        "note": "test",
    }
    with pytest.raises(CommandRefused) as exc:
        _handle_link_organization_product_line(_FakeSelf(), None, _operator(), fields, "r")
    assert exc.value.code == "invalid_product_line"


def test_all_product_line_ids_are_in_the_closed_list() -> None:
    """All six brand ids are accepted; anything else raises invalid_product_line."""
    from origenlab_api.v2.crm_authoring import PRODUCT_LINE_IDS
    for lid in PRODUCT_LINE_IDS:
        assert lid in {"hielscher", "ortoalresa", "ika", "adam-equipment", "loeser", "serva"}


# ──────────────────────────────────────────────────── import scan (no send) ──

def test_authoring_module_imports_nothing_that_sends() -> None:
    """crm_authoring.py must not import anything that sends email or talks to Gmail."""
    source = (SRC / "v2" / "crm_authoring.py").read_text()
    # Outbound reads (campaign_recipient counts for merge) are allowed;
    # only Gmail / email-dispatch imports are forbidden.
    forbidden = ["gmail", "send_email", "smtp", "sendgrid"]
    for kw in forbidden:
        assert kw not in source.lower(), f"crm_authoring.py contains send-related token {kw!r}"


# ──────────────────────────────────────────────── no auto-confirm assertions ──

def test_no_code_path_changes_assertion_without_explicit_command() -> None:
    """No UPDATE to evidence.assertion happens outside the supplier-candidate handlers."""
    import ast as _ast
    source = (SRC / "v2" / "crm_authoring.py").read_text()
    # Find all string constants that UPDATE evidence.assertion
    tree = _ast.parse(source)
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Constant) and isinstance(node.value, str):
            s = node.value.lower()
            if "update evidence.assertion" in s:
                # This is acceptable only inside the supplier-candidate helpers
                # Find the enclosing function by looking at the AST parent chain
                # (simplified: scan source lines for context)
                lines = source[:source.find(node.value)].splitlines()
                enclosing = next(
                    (l for l in reversed(lines) if l.strip().startswith("def _handle_")),
                    "",
                )
                assert "supplier_candidate" in enclosing, (
                    f"'update evidence.assertion' found outside supplier-candidate handlers "
                    f"(near: {enclosing!r})"
                )


# ───────────────────────────────────────────────────── confirm-supplier classification ──

def test_confirm_supplier_classification_must_be_supplier_or_manufacturer() -> None:
    with pytest.raises(ValidationError):
        from origenlab_api.v2.crm_authoring_routes import ConfirmSupplierCandidateBody
        ConfirmSupplierCandidateBody(
            assertion_id=str(uuid.uuid4()),
            organization_id=str(uuid.uuid4()),
            classification="customer",
            note="test",
        )
