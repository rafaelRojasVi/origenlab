"""Enforce public GET route response_model coverage and route inventory parity."""

# ruff: noqa: E402

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.routing import APIRoute

from origenlab_api.main import create_app

# Mirrors apps/api/docs/API_RESPONSE_CONTRACT.md — "Route inventory (GET, public)".
EXPECTED_PUBLIC_GET_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/operator/status",
        "/operator/automation-status",
        "/emails/recent",
        "/cases/warm",
        "/opportunities/commercial",
        "/opportunities/commercial/{opportunity_id}",
        "/operations/work-queue",
        "/operations/sales-opportunities",
        "/operations/sales-opportunities/{sales_opportunity_id}",
        "/operations/sales-opportunities/{sales_opportunity_id}/activities",
        "/operations/sales-opportunities/{sales_opportunity_id}/tasks",
        "/operations/sales-opportunities/{sales_opportunity_id}/quotes",
        "/operations/customer-quotes",
        "/operations/customer-quotes/drive-pending",
        "/operations/customer-quotes/{quote_id}",
        "/operations/opportunities/{opportunity_id}/state",
        "/operations/opportunities/{opportunity_id}/activities",
        "/operations/opportunities/{opportunity_id}/tasks",
        "/operator/procurement/status",
        "/operator/procurement/institutions",
        "/operator/procurement/institutions/{institution_id}",
        "/operator/procurement/queues/{queue_name}",
        "/operator/procurement/tenders/{tender_code}",
        "/operator/procurement/tenders/{tender_code}/attachment-navigation",
        "/contacts/{email}",
        "/mirror/health/dependencies",
        "/mirror/meta/dashboard-sync",
        "/mirror/audits/gmail-interactions",
        "/mirror/dashboard/summary",
        "/mirror/classification/summary",
        "/mirror/classification/recent",
        "/mirror/classification/actions",
        "/mirror/commercial/purchase-events",
        "/mirror/commercial/purchase-events/{event_id}",
        "/mirror/commercial/deals",
        "/mirror/commercial/deals/{deal_key}",
        "/mirror/catalog/products",
        "/mirror/catalog/products/{product_key}",
        "/mirror/leads/prospects",
        "/mirror/leads/prospects/export.csv",
        "/mirror/leads/prospects/{prospect_key}",
        "/mirror/leads/summary",
        "/mirror/contacts",
        "/mirror/organizations",
        "/mirror/outbound/suppressions/emails",
        "/mirror/outbound/contact-state",
        "/mirror/outbound/readiness",
    }
)

SKIP_DOC_PATHS: frozenset[str] = frozenset(
    {
        "/docs",
        "/redoc",
        "/openapi.json",
    }
)

PUBLIC_GET_PATH_PREFIXES: tuple[str, ...] = (
    "/operator/",
    "/cases/",
    "/emails/",
    "/contacts/",
    "/opportunities/",
    "/operations/",
    "/mirror/",
)

# Routes allowed without response_model — must be documented in API_RESPONSE_CONTRACT.md.
RESPONSE_MODEL_ALLOWLIST: frozenset[str] = frozenset(
    {
        "/mirror/leads/prospects/export.csv",
    }
)


def _contract_doc_path() -> Path:
    return Path(__file__).resolve().parents[1] / "docs" / "API_RESPONSE_CONTRACT.md"


def parse_public_get_paths_from_contract() -> frozenset[str]:
    text = _contract_doc_path().read_text(encoding="utf-8")
    marker = "## Route inventory (GET, public)"
    if marker not in text:
        raise AssertionError(f"contract doc missing section: {marker}")
    section = text.split(marker, 1)[1]
    end = section.find("\n---\n")
    if end != -1:
        section = section[:end]
    paths: set[str] = set()
    for line in section.splitlines():
        if not re.match(r"^\|\s*GET\s*\|", line):
            continue
        match = re.search(r"`(/[^`]+)`", line)
        if match:
            paths.add(match.group(1))
    return frozenset(paths)


def is_public_get_path(path: str) -> bool:
    if path in SKIP_DOC_PATHS:
        return False
    if path == "/health":
        return True
    return path.startswith(PUBLIC_GET_PATH_PREFIXES)


def collect_public_get_route_contexts(app: Any) -> list[tuple[str, Any]]:
    """Return (full_path, effective_route_context) for public GET API routes."""
    collected: list[tuple[str, Any]] = []
    for mount in app.router.routes:
        contexts_factory = getattr(mount, "effective_route_contexts", None)
        if contexts_factory is None:
            continue
        for ctx in contexts_factory():
            if "GET" not in ctx.methods:
                continue
            path = str(ctx.path)
            if not is_public_get_path(path):
                continue
            collected.append((path, ctx))
    return collected


def collect_public_get_api_routes(app: Any) -> list[tuple[str, APIRoute]]:
    """Fallback collector walking included routers when effective contexts are unavailable."""
    collected: list[tuple[str, APIRoute]] = []

    def walk(routes: list[Any], prefix: str = "") -> None:
        for route in routes:
            original_router = getattr(route, "original_router", None)
            if original_router is not None:
                walk(original_router.routes, prefix)
                continue
            if not isinstance(route, APIRoute):
                continue
            if "GET" not in route.methods:
                continue
            full_path = prefix + route.path
            if not is_public_get_path(full_path):
                continue
            collected.append((full_path, route))

    walk(app.router.routes)
    return collected


def public_get_paths_from_app(app: Any) -> frozenset[str]:
    contexts = collect_public_get_route_contexts(app)
    if contexts:
        return frozenset(path for path, _ in contexts)
    return frozenset(path for path, _ in collect_public_get_api_routes(app))


def test_public_get_routes_have_response_model() -> None:
    app = create_app()
    contexts = collect_public_get_route_contexts(app)
    assert contexts, "expected public GET routes from create_app()"

    missing: list[str] = []
    for path, ctx in contexts:
        if path in RESPONSE_MODEL_ALLOWLIST:
            continue
        if ctx.response_model is None:
            missing.append(path)

    assert not missing, (
        "public GET routes missing response_model (add schema or RESPONSE_MODEL_ALLOWLIST "
        f"with contract doc exception): {sorted(missing)}"
    )


def test_public_get_route_inventory_matches_app() -> None:
    app = create_app()
    actual = public_get_paths_from_app(app)
    assert actual == EXPECTED_PUBLIC_GET_PATHS


def test_public_get_route_inventory_matches_contract_doc() -> None:
    documented = parse_public_get_paths_from_contract()
    assert documented == EXPECTED_PUBLIC_GET_PATHS
