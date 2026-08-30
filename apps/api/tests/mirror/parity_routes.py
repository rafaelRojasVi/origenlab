"""Canonical legacy → mirror route pairs (API-3 Phase 2 parity; enforced by tests)."""

from __future__ import annotations

# (legacy_path, mirror_path, brief note)
LEGACY_TO_MIRROR_ROUTE_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("/health/dependencies", "/mirror/health/dependencies", "Postgres + SQLite dependency ping"),
    ("/meta/dashboard-sync", "/mirror/meta/dashboard-sync", "Latest dashboard sync watermark"),
    ("/dashboard/summary", "/mirror/dashboard/summary", "scope=canonical|archive"),
    ("/classification/summary", "/mirror/classification/summary", "Canonical classification KPIs"),
    ("/classification/recent", "/mirror/classification/recent", "label, limit"),
    ("/classification/actions", "/mirror/classification/actions", "Grouped triage actions"),
    ("/commercial/purchase-events", "/mirror/commercial/purchase-events", "limit 1–100"),
    (
        "/commercial/purchase-events/{event_id}",
        "/mirror/commercial/purchase-events/{event_id}",
        "Path param renamed event_id on mirror; same semantics",
    ),
    ("/commercial/deals", "/mirror/commercial/deals", "limit 1–100; redacted deal ledger"),
    (
        "/commercial/deals/{deal_key}",
        "/mirror/commercial/deals/{deal_key}",
        "Redacted commercial deal detail by deal_key",
    ),
    ("/contacts", "/mirror/contacts", "Paginated mart list; not operator detail"),
    ("/organizations", "/mirror/organizations", "Paginated mart list"),
    ("/outbound/suppressions/emails", "/mirror/outbound/suppressions/emails", "Email suppressions"),
    ("/outbound/contact-state", "/mirror/outbound/contact-state", "Outreach contact state"),
    ("/outbound/readiness", "/mirror/outbound/readiness", "Read-only readiness report"),
)

REQUIRED_MIRROR_OPENAPI_PATHS: tuple[str, ...] = tuple(
    mirror for _legacy, mirror, _note in LEGACY_TO_MIRROR_ROUTE_PAIRS
)

# Legacy Slice-1 GET /health — no /mirror alias; apps/api GET /health is operator contract.
LEGACY_HEALTH_NO_MIRROR_ALIAS = "/health"

OPERATOR_TODAY_PATHS: tuple[str, ...] = (
    "/health",
    "/operator/status",
    "/operator/automation-status",
    "/cases/warm",
    "/operator/procurement/status",
    "/contacts/{email}",
    "/emails/recent",
)
