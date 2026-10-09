"""CRM authoring reads — people, organizations, merge preview.

In-process tests (fake-repo, no database):

* Shape: every key from the CONTRACT is present.
* Viewer masking: ``X-OrigenLab-Redaction`` header set, no bare ``@`` in JSON body.
* 404 for unknown ids; 422 for same id merge-preview.

Database-backed tests (opt-in, module-scoped disposable cluster):

* Seed: a person with 2 contact points (one inactive), an affiliation to an org,
  3 notes forming a revision chain (one archived), a campaign_recipient referencing the person.
* An organization with identifiers/domains (one soft-removed), classifications, a product line.
* Every list's length equals the count the same read reports (totals = lists).
* Viewer gets masked contact points and no raw '@' anywhere in the JSON body.

Every name, address, number and hash below is invented; `.test` is a reserved TLD.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository
from origenlab_api.v2.crm_workspace_routes import workspace_router
from origenlab_api.v2.crm_merge_preview import merge_preview_digest

# ─────────────────────────────────────── helpers / fake identity ──────────────


class _Identity:
    def __init__(self, role: str, op_id: str) -> None:
        self._role = role
        self._op_id = op_id

    def resolve(self, headers: Any) -> Any:  # noqa: ANN401
        from origenlab_api.v2.identity import OperatorIdentity
        return OperatorIdentity(
            operator_id=self._op_id,
            email_norm=f"{self._role}@example.test",
            display_name=self._role.capitalize(),
            role=self._role,
            status="active",
        )


class _RefusingIdentity:
    def resolve(self, headers: Any) -> Any:  # noqa: ANN401
        from origenlab_api.v2.identity import IdentityRefused
        raise IdentityRefused("no credential")


_FAKE_OP = str(uuid.uuid4())


# ─────────────────────────────────────── in-process fake-repo ─────────────────


class _FakeRepo:
    """Minimal stand-in for the DB repository; no SQL executed."""

    def person_authoring(self, person_id: str) -> dict[str, Any] | None:
        if person_id == "00000000-0000-4000-8000-000000000001":
            return None
        return {
            "person": {
                "id": person_id, "display_name": "Ana Ejemplo", "given_name": "Ana",
                "family_name": "Ejemplo", "title": None, "status": "active",
                "archived_at": None, "archive_reason": None, "confirmation": "confirmed",
                "version": 1, "created_at": "2026-01-01T00:00:00Z",
                "updated_at": "2026-01-01T00:00:00Z", "merged_into_person_id": None,
            },
            "contact_points": [
                {"id": str(uuid.uuid4()), "kind": "email", "value_display": "ana@ejemplo.test",
                 "value_norm": "ana@ejemplo.test", "usage": "work", "status": "active",
                 "version": 1, "note": None, "deactivated_at": None,
                 "created_at": "2026-01-01T00:00:00Z"},
            ],
            "affiliations": [
                {"id": str(uuid.uuid4()), "organization_id": str(uuid.uuid4()),
                 "organization_name": "Instituto Ejemplo", "role_title": "Investigadora",
                 "unit_label": None, "valid_from": "2026-01-01", "valid_to": None,
                 "confirmation": "confirmed", "note": None},
            ],
            "notes": [
                {"id": str(uuid.uuid4()), "root_note_id": None, "revision_no": 1,
                 "body": "Nota de prueba.", "author_operator_id": _FAKE_OP,
                 "author_name": "Sales", "created_at": "2026-01-01T00:00:00Z",
                 "status": "active", "archived_at": None, "archive_reason": None,
                 "version": 1, "is_latest": True},
            ],
            "references": {
                "campaign_recipients": 2, "opportunity_participants": 1,
                "quotes": 0, "evidence_assertions": 0, "notes": 1,
            },
            "removal": {"allowed": False, "reasons": ["Es destinatario en 2 campañas"]},
            "authoring": None,
        }

    def organization_authoring(self, org_id: str) -> dict[str, Any] | None:
        if org_id == "00000000-0000-4000-8000-000000000002":
            return None
        return {
            "organization": {
                "id": org_id, "name": "Instituto Ejemplo", "legal_name": None,
                "kind": "institution", "status": "active", "archived_at": None,
                "archive_reason": None, "confirmation": "confirmed", "version": 1,
                "merged_into_organization_id": None, "created_at": "2026-01-01T00:00:00Z",
            },
            "identifiers": [],
            "domains": [
                {"id": str(uuid.uuid4()), "domain_norm": "instituto.test", "scope": "exclusive",
                 "removed_at": None, "remove_reason": None},
            ],
            "classifications": [
                {"id": str(uuid.uuid4()), "role": "customer", "valid_from": "2026-01-01",
                 "valid_to": None, "note": None},
            ],
            "product_lines": [],
            "contact_points": [],
            "people": [
                {"person_id": str(uuid.uuid4()), "display_name": "Ana Ejemplo",
                 "role_title": "Investigadora", "valid_from": "2026-01-01",
                 "valid_to": None, "affiliation_id": str(uuid.uuid4())},
            ],
            "notes": [],
            "references": {
                "campaign_recipients": 0, "opportunities": 1, "quotes": 0,
                "affiliations": 1, "evidence_assertions": 0, "catalog_products": 0,
            },
            "removal": {"allowed": False, "reasons": ["Participa en 1 caso comercial"]},
            "authoring": None,
        }

    def providers(self) -> dict[str, Any]:
        return {"on_cases": [], "candidates": [], "lines": []}

    def opportunity_notes(self, opportunity_id: str) -> dict[str, Any] | None:
        if opportunity_id == "00000000-0000-4000-8000-000000000003":
            return None
        return {
            "opportunity_id": opportunity_id,
            "notes": [
                {"id": str(uuid.uuid4()), "root_note_id": None, "revision_no": 1,
                 "body": "Llamé al laboratorio; esperan la orden de compra.",
                 "author_operator_id": _FAKE_OP, "author_name": "Sales",
                 "created_at": "2026-10-01T00:00:00Z", "status": "active", "archived_at": None,
                 "archive_reason": None, "version": 1, "is_latest": True},
            ],
        }

    def opportunity_quote_candidates(self, opportunity_id: str) -> dict[str, Any] | None:
        if opportunity_id == "00000000-0000-4000-8000-000000000003":
            return None
        return {"opportunity_id": opportunity_id, "candidates": [
            {"source_record_id": "00000000-0000-4000-8000-000000000010",
             "quote_token": "CN01259", "filename": "CN01259-ficticio.pdf",
             "sent_at": "2026-10-07T12:00:00Z", "subject": "Cotización ficticia",
             "document_sha256": "ab" * 32, "gmail_url": "https://mail.google.com/mail/u/0/#all/abc",
             "reason": "Número exacto y destinatario externo compartido", "recorded_elsewhere": False},
        ]}

    def opportunity_purchase_order_candidates(self, opportunity_id: str) -> dict[str, Any] | None:
        if opportunity_id == "00000000-0000-4000-8000-000000000003":
            return None
        return {"opportunity_id": opportunity_id, "candidates": [
            {"source_record_id": "00000000-0000-4000-8000-000000000020",
             "purchase_order_number": "55512345", "filename": "OC 55512345.pdf",
             "gmail_url": "https://mail.google.com/mail/?authuser=contacto%40origenlab.cl#all/test",
             "subject": "OC 55512345", "document_sha256": "ab" * 32,
             "reason": "Coincidencia externa", "sent_at": "2026-10-09T12:00:00Z"},
        ]}

    def opportunity_mail_documents(self, opportunity_id: str) -> dict[str, Any] | None:
        if opportunity_id == "00000000-0000-4000-8000-000000000003":
            return None
        return {
            "opportunity_id": opportunity_id,
            "messages": [
                {"source_record_id": str(uuid.uuid4()), "subject": "Cotización equipo",
                 "sent_at": "2026-10-01T12:00:00+00:00",
                 "documents": [{"sha256": "ab" * 32, "filename": "CN01239.pdf",
                                "cn_tokens": ["CN01239"], "recorded": None}]},
            ],
        }


def _app(role: str = "sales", repo: Any = None, authoring_enabled: bool = False) -> FastAPI:
    app = FastAPI()
    app.state.v2_identity = _Identity(role, _FAKE_OP)
    app.state.crm_workspace = repo or _FakeRepo()
    app.state.crm_authoring_enabled = authoring_enabled
    app.include_router(workspace_router)
    return app


# ─────────────────────────────────── in-process shape tests ───────────────────


def test_person_authoring_shape() -> None:
    client = TestClient(_app("sales"))
    r = client.get("/v2/workspace/people/00000000-0000-4000-8001-000000000001")
    assert r.status_code == 200
    body = r.json()
    assert "person" in body
    assert "contact_points" in body
    assert "affiliations" in body
    assert "notes" in body
    assert "references" in body
    assert "removal" in body
    assert "authoring" in body
    # removal is never allowed (physical removal is not an option)
    assert body["removal"]["allowed"] is False
    assert body["removal"]["reasons"]
    # authoring flag injected by route
    assert body["authoring"]["enabled"] is False


def test_person_authoring_enabled_flag() -> None:
    client = TestClient(_app("sales", authoring_enabled=True))
    r = client.get("/v2/workspace/people/00000000-0000-4000-8001-000000000001")
    body = r.json()
    assert body["authoring"]["enabled"] is True
    assert body["authoring"]["may_author"] is True
    assert body["authoring"]["may_archive"] is False  # sales, not admin


def test_person_authoring_admin_may_archive() -> None:
    client = TestClient(_app("admin", authoring_enabled=True))
    r = client.get("/v2/workspace/people/00000000-0000-4000-8001-000000000001")
    body = r.json()
    assert body["authoring"]["may_archive"] is True


def test_person_authoring_viewer_masked() -> None:
    client = TestClient(_app("viewer"))
    r = client.get("/v2/workspace/people/00000000-0000-4000-8001-000000000001")
    assert r.status_code == 200
    # The redaction header must be present
    assert r.headers.get("x-origenlab-redaction") == "contact-addresses"
    # No raw @ character allowed in the JSON body
    body_text = r.text
    # Find all @ signs and check none are raw addresses
    matches = re.findall(r"[^\s@<>()\[\],;:\"]+@(?=[A-Za-z0-9.\-]*[A-Za-z])", body_text)
    for m in matches:
        # All must be masked (start with ***)
        assert m.startswith("***"), f"Unmasked address in viewer response: {m}"


def test_person_authoring_404() -> None:
    client = TestClient(_app("sales"))
    r = client.get("/v2/workspace/people/00000000-0000-4000-8000-000000000001")
    assert r.status_code == 404


def test_person_authoring_requires_operator() -> None:
    app = FastAPI()
    app.state.v2_identity = _RefusingIdentity()
    app.state.crm_workspace = _FakeRepo()
    app.include_router(workspace_router)
    client = TestClient(app)
    r = client.get("/v2/workspace/people/00000000-0000-4000-8001-000000000001")
    assert r.status_code == 401


def test_organization_authoring_shape() -> None:
    client = TestClient(_app("sales"))
    r = client.get("/v2/workspace/organizations/00000000-0000-4000-8001-000000000002/authoring")
    assert r.status_code == 200
    body = r.json()
    assert "organization" in body
    assert "identifiers" in body
    assert "domains" in body
    assert "classifications" in body
    assert "product_lines" in body
    assert "contact_points" in body
    assert "people" in body
    assert "notes" in body
    assert "references" in body
    assert "removal" in body
    assert "authoring" in body
    assert body["removal"]["allowed"] is False
    refs = body["references"]
    assert "campaign_recipients" in refs
    assert "opportunities" in refs
    assert "quotes" in refs
    assert "affiliations" in refs
    assert "evidence_assertions" in refs
    assert "catalog_products" in refs


def test_organization_authoring_404() -> None:
    client = TestClient(_app("sales"))
    r = client.get("/v2/workspace/organizations/00000000-0000-4000-8000-000000000002/authoring")
    assert r.status_code == 404


def test_providers_has_lines_and_authoring() -> None:
    client = TestClient(_app("sales"))
    r = client.get("/v2/workspace/providers")
    assert r.status_code == 200
    body = r.json()
    assert "lines" in body
    assert "authoring" in body
    assert "enabled" in body["authoring"]


@pytest.mark.parametrize("role", ["viewer", "sales", "admin"])
def test_case_notes_read_in_the_note_shape_for_every_role(role: str) -> None:
    """«Registrar seguimiento» reads the case's notes; reading them is anyone's, writing is not."""
    client = TestClient(_app(role))
    case_id = "00000000-0000-4000-8001-000000000009"
    r = client.get(f"/v2/workspace/opportunities/{case_id}/notes")
    assert r.status_code == 200
    body = r.json()
    assert body["opportunity_id"] == case_id
    assert set(body["notes"][0]) == {
        "id", "root_note_id", "revision_no", "body", "author_operator_id", "author_name",
        "created_at", "status", "archived_at", "archive_reason", "version", "is_latest",
    }


def test_case_notes_of_a_missing_case_are_404_and_a_bad_id_422() -> None:
    client = TestClient(_app("sales"))
    assert client.get("/v2/workspace/opportunities/00000000-0000-4000-8000-000000000003/notes").status_code == 404
    assert client.get("/v2/workspace/opportunities/not-a-uuid/notes").status_code == 422


def test_case_notes_need_an_operator() -> None:
    app = _app("sales")
    app.state.v2_identity = _RefusingIdentity()
    r = TestClient(app).get("/v2/workspace/opportunities/00000000-0000-4000-8001-000000000009/notes")
    assert r.status_code == 401


@pytest.mark.parametrize("role", ["viewer", "sales", "admin"])
def test_case_mail_documents_read_for_every_role(role: str) -> None:
    """«Registrar cotización» picks from the case's linked messages; reading them is anyone's."""
    client = TestClient(_app(role))
    case_id = "00000000-0000-4000-8001-000000000009"
    r = client.get(f"/v2/workspace/opportunities/{case_id}/mail-documents")
    assert r.status_code == 200
    body = r.json()
    assert body["opportunity_id"] == case_id
    assert set(body["messages"][0]) == {"source_record_id", "subject", "sent_at", "documents"}
    assert set(body["messages"][0]["documents"][0]) == {"sha256", "filename", "cn_tokens", "recorded"}


def test_case_mail_documents_of_a_missing_case_are_404_a_bad_id_422_and_need_an_operator() -> None:
    client = TestClient(_app("sales"))
    assert client.get(
        "/v2/workspace/opportunities/00000000-0000-4000-8000-000000000003/mail-documents").status_code == 404
    assert client.get("/v2/workspace/opportunities/not-a-uuid/mail-documents").status_code == 422
    app = _app("sales")
    app.state.v2_identity = _RefusingIdentity()
    assert TestClient(app).get(
        "/v2/workspace/opportunities/00000000-0000-4000-8001-000000000009/mail-documents").status_code == 401


@pytest.mark.parametrize("role", ["viewer", "sales", "admin"])
def test_cross_thread_quote_candidates_are_read_only_and_scoped(role: str) -> None:
    client = TestClient(_app(role))
    case_id = "00000000-0000-4000-8001-000000000009"
    path = f"/v2/workspace/opportunities/{case_id}/quote-candidates"
    r = client.get(path)
    if role == "viewer":
        assert r.status_code == 403
        assert "candidates" not in r.json()
    else:
        assert r.status_code == 200
        assert r.json()["candidates"][0]["quote_token"] == "CN01259"
    assert client.post(path).status_code == 405
    assert client.get(
        "/v2/workspace/opportunities/00000000-0000-4000-8000-000000000003/quote-candidates"
    ).status_code == (403 if role == "viewer" else 404)
    assert client.get("/v2/workspace/opportunities/not-a-uuid/quote-candidates").status_code == 422


@pytest.mark.parametrize("role", ["viewer", "sales", "admin"])
def test_purchase_order_candidates_require_sales_and_never_write(role: str) -> None:
    client = TestClient(_app(role))
    cid = "00000000-0000-4000-8001-000000000009"
    path = f"/v2/workspace/opportunities/{cid}/purchase-order-candidates"
    response = client.get(path)
    if role == "viewer":
        assert response.status_code == 403
        assert "candidates" not in response.json()
    else:
        assert response.status_code == 200
        assert response.json()["candidates"][0]["purchase_order_number"] == "55512345"
    assert client.post(path).status_code == 405
    assert client.get(
        "/v2/workspace/opportunities/00000000-0000-4000-8000-000000000003/purchase-order-candidates"
    ).status_code == (403 if role == "viewer" else 404)
    assert client.get("/v2/workspace/opportunities/not-a-uuid/purchase-order-candidates").status_code == 422


def test_quote_candidates_require_an_operator() -> None:
    app = _app("sales")
    app.state.v2_identity = _RefusingIdentity()
    assert TestClient(app).get(
        "/v2/workspace/opportunities/00000000-0000-4000-8001-000000000009/quote-candidates"
    ).status_code == 401


def test_reads_report_the_switch_that_main_sets() -> None:
    """The reads must answer from the flag `_mount_crm_authoring` writes, not one only tests set.

    They once read `app.state.v2_crm_authoring_enabled`, which nothing in the app assigns, so
    every read reported authoring off while all 28 commands were mounted.
    """
    from origenlab_api import main
    from origenlab_api.settings import Settings

    loopback = "postgresql://origenlab_api:pw@127.0.0.1:54332/origenlab_dev"

    def never_connects(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a read-shape test must not open a connection")

    app = FastAPI()
    app.state.v2_identity = _Identity("sales", _FAKE_OP)
    app.state.crm_workspace = _FakeRepo()
    settings = Settings(_env_file=None, v2_database_url=loopback, v2_crm_authoring_enabled=True)
    main._mount_crm_authoring(app, settings, loopback, never_connects)
    app.include_router(workspace_router)
    client = TestClient(app)

    assert client.get("/v2/workspace/providers").json()["authoring"]["enabled"] is True
    person = client.get("/v2/workspace/people/00000000-0000-4000-8001-000000000001").json()
    assert person["authoring"] == {"enabled": True, "may_author": True, "may_archive": False}


# ─────────────────────────────────── merge preview (in-process) ───────────────


def test_merge_preview_same_id_is_422() -> None:
    client = TestClient(_app("sales"))
    pid = "00000000-0000-4000-8001-000000000001"
    r = client.get(f"/v2/workspace/people/merge-preview?loser={pid}&winner={pid}")
    assert r.status_code == 422


def test_merge_preview_missing_param_is_422() -> None:
    client = TestClient(_app("sales"))
    r = client.get("/v2/workspace/people/merge-preview?loser=00000000-0000-4000-8001-000000000001")
    assert r.status_code == 422


def test_merge_preview_digest_is_stable() -> None:
    """The digest of a preview must not change between calls with the same inputs."""
    preview_a: dict[str, Any] = {
        "loser":  {"id": "aaa", "display_name": "A", "version": 1},
        "winner": {"id": "bbb", "display_name": "B", "version": 2},
        "moves": {"contact_points": 1, "affiliations": 0, "opportunity_participants": 0,
                  "campaign_recipients": 3, "notes": 0},
        "conflicts": [],
    }
    preview_a["preview_sha256"] = merge_preview_digest(preview_a)
    preview_b: dict[str, Any] = {**preview_a}
    preview_b["preview_sha256"] = merge_preview_digest(preview_b)
    assert preview_a["preview_sha256"] == preview_b["preview_sha256"]


def test_merge_preview_digest_changes_with_version() -> None:
    preview_v1: dict[str, Any] = {
        "loser":  {"id": "aaa", "display_name": "A", "version": 1},
        "winner": {"id": "bbb", "display_name": "B", "version": 2},
        "moves": {"contact_points": 0, "affiliations": 0, "opportunity_participants": 0,
                  "campaign_recipients": 0, "notes": 0},
        "conflicts": [],
    }
    preview_v2: dict[str, Any] = {
        "loser":  {"id": "aaa", "display_name": "A", "version": 2},
        "winner": {"id": "bbb", "display_name": "B", "version": 2},
        "moves": preview_v1["moves"],
        "conflicts": [],
    }
    assert merge_preview_digest(preview_v1) != merge_preview_digest(preview_v2)


# ─────────────────────────────────────── database-backed tests ────────────────

try:
    from v2_command_harness import build_disposable_database, needs_db, runtime_dsn
except ImportError:  # pragma: no cover
    needs_db = pytest.mark.skip(reason="v2_command_harness not available")

    def build_disposable_database():  # type: ignore[misc]
        yield ""

    def runtime_dsn(x: str) -> str:  # type: ignore[misc]
        return x


ABSENT = "00000000-0000-4000-8000-0000deadbeef"


@pytest.fixture(scope="module")
def disposable_database():
    yield from build_disposable_database()


def _insert(cur: Any, sql: str, params: tuple) -> str:
    cur.execute(sql + " returning id::text", params)
    return cur.fetchone()[0]


@pytest.fixture(scope="module")
def world(disposable_database: str) -> dict[str, str]:
    """Seed: person, org, contact points, affiliation, notes, campaign, recipient, product line."""
    import psycopg

    tag = uuid.uuid4().hex[:10]
    w: dict[str, str] = {"tag": tag}

    with psycopg.connect(disposable_database) as conn, conn.cursor() as cur:
        cur.execute("set role origenlab_owner")

        op = w["op"] = _insert(
            cur,
            "insert into platform.operator (auth_user_id, email_norm, display_name, role, status)"
            " values (gen_random_uuid(), %s, 'Test Op', 'sales', 'active')",
            (f"testop-{tag}@example.test",),
        )

        sr = w["sr"] = _insert(
            cur,
            "insert into evidence.source_record (kind, dedupe_key, payload, source_uri)"
            " values ('gmail_message', %s, '{}'::jsonb, %s)",
            (f"auth-{tag}", f"gmail://msg/{tag}"),
        )

        # --- organization ---
        org = w["org"] = _insert(
            cur,
            "insert into crm.organization (kind, name, confirmation, confirmed_by_operator_id)"
            " values ('institution', %s, 'confirmed', %s)",
            (f"Inst {tag}", op),
        )

        # External identifier (one active, one soft-removed)
        ident_active = w["ident_active"] = _insert(
            cur,
            "insert into crm.external_identifier (organization_id, scheme, value_norm)"
            " values (%s, 'rut', %s)",
            (org, f"CL-{tag}-ACTIVE"),
        )
        ident_removed = w["ident_removed"] = _insert(
            cur,
            "insert into crm.external_identifier (organization_id, scheme, value_norm,"
            " removed_at, removed_by_operator_id, remove_reason)"
            " values (%s, 'rut', %s, now(), %s, 'Duplicado')",
            (org, f"CL-{tag}-REMOVED", op),
        )

        # Domain (one active, one removed)
        domain_active = w["domain_active"] = _insert(
            cur,
            "insert into crm.organization_domain (organization_id, domain_norm, scope)"
            " values (%s, %s, 'shared')",
            (org, f"inst-{tag}.test"),
        )
        domain_removed = w["domain_removed"] = _insert(
            cur,
            "insert into crm.organization_domain (organization_id, domain_norm, scope,"
            " removed_at, removed_by_operator_id, remove_reason)"
            " values (%s, %s, 'shared', now(), %s, 'Expirado')",
            (org, f"old-{tag}.test", op),
        )

        # Classification
        class_id = w["class"] = _insert(
            cur,
            "insert into crm.organization_relationship (organization_id, role, valid_from,"
            " origin_source_record_id)"
            " values (%s, 'customer', current_date, %s)",
            (org, sr),
        )

        # Product line link
        pl = w["pl"] = _insert(
            cur,
            "insert into crm.organization_product_line (organization_id, line_id, linked_by_operator_id)"
            " values (%s, 'hielscher', %s)",
            (org, op),
        )

        # --- person ---
        person = w["person"] = _insert(
            cur,
            "insert into crm.person (display_name, confirmation, confirmed_by_operator_id)"
            " values (%s, 'confirmed', %s)",
            (f"Ana {tag}", op),
        )
        person2 = w["person2"] = _insert(
            cur,
            "insert into crm.person (display_name, confirmation, confirmed_by_operator_id)"
            " values (%s, 'confirmed', %s)",
            (f"Bob {tag}", op),
        )

        # Contact points: one active (email, work = person+org), one inactive (personal)
        cp_active = w["cp_active"] = _insert(
            cur,
            "insert into crm.contact_point (person_id, organization_id, kind, value_display,"
            " value_norm, usage, confirmation)"
            " values (%s, %s, 'email', %s, %s, 'work', 'confirmed')",
            (person, org, f"ana-{tag}@inst-{tag}.test", f"ana-{tag}@inst-{tag}.test"),
        )
        cp_inactive = w["cp_inactive"] = _insert(
            cur,
            "insert into crm.contact_point (person_id, kind, value_display, value_norm, usage,"
            " confirmation, status, deactivated_at, deactivated_by_operator_id)"
            " values (%s, 'email', %s, %s, 'personal', 'confirmed', 'inactive', now(), %s)",
            (person, f"old-ana-{tag}@old-{tag}.test", f"old-ana-{tag}@old-{tag}.test", op),
        )

        # Affiliation: person → org
        aff = w["aff"] = _insert(
            cur,
            "insert into crm.affiliation (person_id, organization_id, role_title, valid_from,"
            " confirmation, confirmed_by_operator_id)"
            " values (%s, %s, 'Investigadora', current_date, 'confirmed', %s)",
            (person, org, op),
        )

        # Notes (revision chain): note1 → note2 (revision) → note3 (archived)
        # Root note
        note1 = w["note1"] = _insert(
            cur,
            "insert into crm.note (subject_kind, subject_id, body, author_operator_id)"
            " values ('person', %s, 'Nota original.', %s)",
            (person, op),
        )
        # Revision of note1
        note2 = w["note2"] = _insert(
            cur,
            "insert into crm.note (subject_kind, subject_id, body, author_operator_id,"
            " revision_of_note_id, root_note_id, revision_no)"
            " values ('person', %s, 'Nota revisada.', %s, %s, %s, 2)",
            (person, op, note1, note1),
        )
        # Archived note (separate chain, not a revision)
        note3 = w["note3"] = _insert(
            cur,
            "insert into crm.note (subject_kind, subject_id, body, author_operator_id,"
            " status, archived_at, archived_by_operator_id, archive_reason, version)"
            " values ('person', %s, 'Nota archivada.', %s, 'archived', now(), %s, 'Obsoleta', 2)",
            (person, op, op),
        )

        # Campaign: archived, imported
        mailbox = w["mailbox"] = _insert(
            cur,
            "insert into comms.mailbox (address_norm, display_name) values (%s, 'OrigenLab')",
            (f"send-{tag}@org.test",),
        )
        campaign_sr = w["campaign_sr"] = _insert(
            cur,
            "insert into evidence.source_record (kind, dedupe_key, payload, source_uri)"
            " values ('migration_manifest', %s, '{\"v1_campaign_id\": \"v1-camp-001\"}'::jsonb, %s)",
            (f"migration_manifest:campaign:{tag}", f"v1://campaign/{tag}"),
        )
        dummy_sha = "a" * 64
        campaign = w["campaign"] = _insert(
            cur,
            "insert into outbound.campaign (name, status, mailbox_id, origin_source_record_id,"
            " content_frozen_at, content_sha256, max_sends)"
            " values (%s, 'archived', %s, %s, now(), %s, 1000)",
            (f"Campaign {tag}", mailbox, campaign_sr, dummy_sha),
        )

        # Campaign recipient referencing the person
        cr = w["cr"] = _insert(
            cur,
            "insert into outbound.campaign_recipient (campaign_id, contact_point_id, person_id,"
            " address_norm, state)"
            " values (%s, %s, %s, %s, 'sent')",
            (campaign, cp_active, person, f"ana-{tag}@inst-{tag}.test"),
        )

        conn.commit()

    return w


@pytest.fixture(scope="module")
def repo_dsn(disposable_database: str) -> str:
    return runtime_dsn(disposable_database)


@needs_db
def test_person_authoring_lists_match_references(disposable_database, world, repo_dsn) -> None:
    """Every list's length equals the count the same read reports."""
    import psycopg

    repo = CrmWorkspaceRepository(psycopg.connect, repo_dsn)
    body = repo.person_authoring(world["person"])
    assert body is not None

    refs = body["references"]
    assert len(body["contact_points"]) == 2  # one active + one inactive
    assert len(body["affiliations"]) == 1
    assert len(body["notes"]) == 3  # note1, note2, note3
    assert refs["notes"] == 3
    assert refs["campaign_recipients"] == 1


@needs_db
def test_person_authoring_is_latest_correct(disposable_database, world, repo_dsn) -> None:
    """note2 is the latest in its chain; note1 is not; note3 is latest in its own chain."""
    import psycopg

    repo = CrmWorkspaceRepository(psycopg.connect, repo_dsn)
    body = repo.person_authoring(world["person"])
    assert body is not None
    notes_by_id = {n["id"]: n for n in body["notes"]}
    assert notes_by_id[world["note1"]]["is_latest"] is False
    assert notes_by_id[world["note2"]]["is_latest"] is True
    assert notes_by_id[world["note3"]]["is_latest"] is True


@needs_db
def test_person_authoring_removal_reasons(disposable_database, world, repo_dsn) -> None:
    import psycopg

    repo = CrmWorkspaceRepository(psycopg.connect, repo_dsn)
    body = repo.person_authoring(world["person"])
    assert body is not None
    assert body["removal"]["allowed"] is False
    # Has campaign recipients → reason should mention campaigns
    reasons_text = " ".join(body["removal"]["reasons"])
    assert "campaña" in reasons_text.lower()


@needs_db
def test_person_authoring_viewer_no_raw_at(disposable_database, world, repo_dsn) -> None:
    """A viewer must get masked addresses and no raw '@' outside the *** prefix."""
    import psycopg

    app = FastAPI()
    app.state.v2_identity = _Identity("viewer", world["op"])
    app.state.crm_workspace = CrmWorkspaceRepository(psycopg.connect, repo_dsn)
    app.state.crm_authoring_enabled = False
    app.include_router(workspace_router)
    client = TestClient(app)

    r = client.get(f"/v2/workspace/people/{world['person']}")
    assert r.status_code == 200
    assert r.headers.get("x-origenlab-redaction") == "contact-addresses"

    body_text = r.text
    # No raw email address (local-part@domain) outside the *** mask
    for m in re.finditer(r"[^\s@<>()\[\],;:\"]+@(?=[A-Za-z0-9.\-]*[A-Za-z])[A-Za-z0-9.\-]+", body_text):
        assert m.group(0).startswith("***"), f"Unmasked address: {m.group(0)}"


@needs_db
def test_person_authoring_sales_sees_addresses(disposable_database, world, repo_dsn) -> None:
    import psycopg

    app = FastAPI()
    app.state.v2_identity = _Identity("sales", world["op"])
    app.state.crm_workspace = CrmWorkspaceRepository(psycopg.connect, repo_dsn)
    app.state.crm_authoring_enabled = False
    app.include_router(workspace_router)
    client = TestClient(app)

    r = client.get(f"/v2/workspace/people/{world['person']}")
    assert r.status_code == 200
    assert "x-origenlab-redaction" not in r.headers
    # The active contact point address should be present in the body
    assert world["tag"] in r.text


@needs_db
def test_organization_authoring_lists_match_references(disposable_database, world, repo_dsn) -> None:
    import psycopg

    repo = CrmWorkspaceRepository(psycopg.connect, repo_dsn)
    body = repo.organization_authoring(world["org"])
    assert body is not None

    # Both identifiers returned (active + removed)
    assert len(body["identifiers"]) == 2
    # Both domains (active + removed)
    assert len(body["domains"]) == 2
    # One classification
    assert len(body["classifications"]) == 1
    # One product line (hielscher)
    assert len(body["product_lines"]) == 1
    assert body["product_lines"][0]["line_id"] == "hielscher"
    assert body["product_lines"][0]["line_name"] == "Hielscher Ultrasonics"
    # One person via affiliation
    assert len(body["people"]) == 1


@needs_db
def test_organization_authoring_removal_reasons(disposable_database, world, repo_dsn) -> None:
    import psycopg

    repo = CrmWorkspaceRepository(psycopg.connect, repo_dsn)
    body = repo.organization_authoring(world["org"])
    assert body is not None
    assert body["removal"]["allowed"] is False


@needs_db
def test_merge_preview_with_db(disposable_database, world, repo_dsn) -> None:
    """Merge preview counts loser's contact points and recipient rows."""
    import psycopg

    app = FastAPI()
    app.state.v2_identity = _Identity("sales", world["op"])
    app.state.crm_workspace = CrmWorkspaceRepository(psycopg.connect, repo_dsn)
    app.state.crm_authoring_enabled = False
    app.include_router(workspace_router)
    client = TestClient(app)

    r = client.get(
        "/v2/workspace/people/merge-preview",
        params={"loser": world["person"], "winner": world["person2"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert "loser" in body
    assert "winner" in body
    assert "moves" in body
    assert "conflicts" in body
    assert "preview_sha256" in body

    moves = body["moves"]
    assert moves["contact_points"] == 2  # both cp_active and cp_inactive belong to loser
    assert moves["campaign_recipients"] == 1
    assert isinstance(body["preview_sha256"], str) and len(body["preview_sha256"]) == 64

    # Digest is stable: compute it again from the same inputs
    expected = merge_preview_digest(body)
    assert body["preview_sha256"] == expected


@needs_db
def test_merge_preview_same_id_404_or_422(disposable_database, world, repo_dsn) -> None:
    import psycopg

    app = FastAPI()
    app.state.v2_identity = _Identity("sales", world["op"])
    app.state.crm_workspace = CrmWorkspaceRepository(psycopg.connect, repo_dsn)
    app.include_router(workspace_router)
    client = TestClient(app)

    r = client.get(
        "/v2/workspace/people/merge-preview",
        params={"loser": world["person"], "winner": world["person"]},
    )
    assert r.status_code == 422


@needs_db
def test_merge_preview_unknown_id_404(disposable_database, world, repo_dsn) -> None:
    import psycopg

    app = FastAPI()
    app.state.v2_identity = _Identity("sales", world["op"])
    app.state.crm_workspace = CrmWorkspaceRepository(psycopg.connect, repo_dsn)
    app.include_router(workspace_router)
    client = TestClient(app)

    r = client.get(
        "/v2/workspace/people/merge-preview",
        params={"loser": ABSENT, "winner": world["person"]},
    )
    assert r.status_code == 404
