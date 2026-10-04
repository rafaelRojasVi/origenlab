"""API mutation surface must remain explicitly allowlisted and narrow."""

from __future__ import annotations

from pathlib import Path

import pytest

from origenlab_api.main import create_app

_API_SRC = Path(__file__).resolve().parents[1] / "src" / "origenlab_api"

# «Enviar prueba» (owner decision 2026-10-04): one admin-only test of one campaign's stored email to
# one address, from contacto@. These are the only files allowed to name the Gmail send client;
# every other file keeps the `gmail_send` guard. The allowance also covers the
# route module, the `main.py` mount, `settings.py` (whose token-file field is named for it) and the
# marketing read in `v2/crm_workspace_routes.py` (which imports the sender address constant), each by
# exact path and nothing broader (never a directory). The one sanctioned sender is
# `POST /v2/commands/send-campaign-test`, mounted only behind its own switch.
_GMAIL_SEND_ALLOWED = frozenset(
    {
        "v2/gmail_send.py",
        "v2/campaign_test_send.py",
        "v2/campaign_test_send_routes.py",
        "v2/crm_workspace_routes.py",
        "main.py",
        "settings.py",
    }
)

_FORBIDDEN_SUBSTRINGS = (
    "refresh_outbound_safety_memory",
    "05_workspace_gmail_imap_to_sqlite",
    "sync_dashboard_postgres_mirror",
    "alembic",
    "gmail_send",
    "send_inline_html",
    "subprocess",
    "build_equipment_first_operator_queue",
    "mark_outreach_state",
)


def test_app_exposes_only_sanctioned_mutating_routes() -> None:
    schema = create_app().openapi()

    expected = {
        (
            "/operator/procurement/tenders/{tender_code}/annex-bundle/preview",
            "post",
        ),
        (
            "/operator/procurement/tenders/{tender_code}/annex-bundle/import",
            "post",
        ),
        (
            "/operations/opportunities/{opportunity_id}/state",
            "post",
        ),
        (
            "/operations/sales-opportunities/promote",
            "post",
        ),
        (
            "/operations/sales-opportunities/manual",
            "post",
        ),
        (
            "/operations/sales-opportunities/{sales_opportunity_id}/stage",
            "post",
        ),
        (
            "/operations/activities",
            "post",
        ),
        (
            "/operations/tasks",
            "post",
        ),
        (
            "/operations/tasks/{task_id}/complete",
            "post",
        ),
        (
            "/operations/tasks/{task_id}/cancel",
            "post",
        ),
        (
            "/operations/sales-opportunities/{sales_opportunity_id}/quotes",
            "post",
        ),
        (
            "/operations/customer-quotes/{quote_id}/drive-workspace",
            "post",
        ),
        (
            "/operations/customer-quotes/{quote_id}/submit-for-review",
            "post",
        ),
        (
            "/operations/customer-quotes/{quote_id}/request-adjustments",
            "post",
        ),
        (
            "/operations/customer-quotes/{quote_id}/approve",
            "post",
        ),
        (
            "/operations/customer-quotes/{quote_id}/confirm-send",
            "post",
        ),
        (
            "/operations/customer-quotes/{quote_id}/close",
            "post",
        ),
        (
            "/operations/sales-opportunities/{sales_opportunity_id}/quotes/adopt-drive-folder",
            "post",
        ),
    }

    mutating_methods = {"post", "put", "patch", "delete"}
    actual: set[tuple[str, str]] = set()

    for path, operations in schema["paths"].items():
        for method in operations:
            normalized = method.lower()
            if normalized in mutating_methods:
                actual.add((path, normalized))

    assert actual == expected


def test_origenlab_api_source_has_no_mutation_script_imports() -> None:
    hits: list[str] = []
    for path in _API_SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in _FORBIDDEN_SUBSTRINGS:
            if needle == "gmail_send" and path.relative_to(_API_SRC).as_posix() in _GMAIL_SEND_ALLOWED:
                continue
            if needle in text:
                hits.append(f"{path.relative_to(_API_SRC)}: {needle}")
    assert hits == [], "forbidden references in apps/api:\n" + "\n".join(hits)


def test_openapi_documents_narrow_operator_mutation_boundary() -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    client = TestClient(create_app())
    r = client.get("/openapi.json")
    assert r.status_code == 200

    description = r.json()["info"]["description"].lower()

    assert "file-backed operator document import" in description
    assert "does not send email" in description
    assert "except the one switch-gated post /v2/commands/send-campaign-test" in description
    assert "sqlite remains read-only" in description
    assert "durable commercial-operations writes" in description
    assert "explicitly allowlisted /operations/* command routes" in description
    assert "trusted operator identity" in description
    assert "other contact/outreach mutations remain outside this api" in description
