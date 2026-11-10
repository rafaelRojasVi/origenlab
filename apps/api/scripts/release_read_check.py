#!/usr/bin/env python3
"""Post-release read self-check: exercises every listed GET route with the real API code.

Connects to production as origenlab_migrator, then SET ROLEs to origenlab_owner, and calls each
read repository method once through fastapi.encoders.jsonable_encoder.  This catches code, SQL
and serialization bugs — such as the Decimal × float TypeError that shipped in #891 and was
fixed by #893 — the moment a deploy finishes, before any operator request triggers them.

This check does NOT catch grant bugs.  The script runs as origenlab_owner (via SET ROLE),
not as origenlab_api; a column or table grant missing for the runtime role would still produce
200 here and 403 in production.  Row-level security is similarly not exercised.

Coverage gates: routes in CHECKED_PATTERNS are exercised here.  Routes in SKIPPED_PATTERNS
are explicitly excluded with a brief reason.  test_release_read_check.py asserts that every
pattern in ALLOWED_UPSTREAM_PATHS falls in one of the two sets, so a newly added proxy route
must be listed here before it can reach production.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from fastapi.encoders import jsonable_encoder

# -- repo imports ----------------------------------------------------------------

from origenlab_api.v2.catalog.reads import V2CatalogReads
from origenlab_api.v2.cockpit_repository import CockpitRepository
from origenlab_api.v2.crm_workspace import CrmWorkspaceRepository
from origenlab_api.v2.finance.reads import FinanceReadRepository
from origenlab_api.v2.invoices.reads import InvoiceReadRepository
from origenlab_api.v2.prospect_findings.reads import ProspectFindingReadRepository
from origenlab_api.v2.quotes.reads import V2QuoteReads
from origenlab_api.v2.repository import V2Repository
from origenlab_api.v2.tenders.reads import TenderReadRepository
from origenlab_api.v2.todos.reads import TodoReadRepository
from origenlab_api.v2.triage_review import TriageReviewRepository

#: The repository's supabase/scripts (this file is apps/api/scripts/release_read_check.py).
HOSTED_ENV_DIR = Path(__file__).resolve().parents[3] / "supabase" / "scripts"

# -- hosted_env is imported lazily in main() so the script is importable in tests
# -- without GITHUB_ACTIONS=true or the supabase/scripts directory on sys.path.


# -- coverage sets ---------------------------------------------------------------
# Every pattern in ALLOWED_UPSTREAM_PATHS must appear in exactly one of these two
# sets.  The strings are the TypeScript regex source between the outer /.../ delimiters.

#: Routes exercised by this script (one call per route).
CHECKED_PATTERNS: frozenset[str] = frozenset(
    [
        # «Finanzas» — the original site of the Decimal × float bug (#891 / #893)
        r"^\/v2\/workspace\/finance\/mes$",
        r"^\/v2\/workspace\/finance\/expenses$",
        r"^\/v2\/workspace\/finance\/supplier-orders$",
        r"^\/v2\/workspace\/finance\/settings$",
        # «Pendientes» and «Pagos»
        r"^\/v2\/workspace\/todos$",
        r"^\/v2\/workspace\/invoices$",
        # Catalog flat reads
        r"^\/v2\/catalog\/products$",
        r"^\/v2\/catalog\/suppliers$",
        r"^\/v2\/catalog\/parameters$",
        r"^\/v2\/catalog\/index$",
        # Cotizador (quote) flat reads
        r"^\/v2\/quotes\/numbering$",
        r"^\/v2\/quotes\/clients$",
        r"^\/v2\/quotes$",
        r"^\/v2\/quotes\/economics$",
        r"^\/v2\/quotes\/product-expenses$",
        # Cockpit and V2 CRM flat reads
        r"^\/v2\/cockpit\/work-queue$",
        r"^\/v2\/contacts$",
        r"^\/v2\/organizations$",
        r"^\/v2\/cases$",
        r"^\/v2\/prospects$",
        r"^\/v2\/opportunities\/active$",
        r"^\/v2\/tasks\/due$",
        r"^\/v2\/review\/summary$",
        r"^\/v2\/quotes\/followup$",
        r"^\/v2\/evidence$",
        r"^\/v2\/evidence\/records$",
        # CRM workspace flat reads
        r"^\/v2\/workspace\/overview$",
        r"^\/v2\/workspace\/pipeline$",
        r"^\/v2\/workspace\/marketing$",
        r"^\/v2\/workspace\/marketing\/analytics$",
        r"^\/v2\/workspace\/marketing\/suppressions$",
        r"^\/v2\/workspace\/marketing\/campaign-blocks$",
        r"^\/v2\/workspace\/providers$",
        r"^\/v2\/workspace\/review$",
        r"^\/v2\/workspace\/operations$",
        r"^\/v2\/workspace\/mail-quote-numbers$",
        r"^\/v2\/workspace\/history$",
        r"^\/v2\/workspace\/quote-readings$",
        r"^\/v2\/workspace\/person-suggestions$",
        r"^\/v2\/workspace\/lookup$",
        # Triage, tenders and prospects
        r"^\/v2\/workspace\/triage-readings$",
        r"^\/v2\/workspace\/forwarded-requests$",
        r"^\/v2\/tenders$",
        r"^\/v2\/tenders\/discovered$",
        r"^\/v2\/prospect-findings$",
        r"^\/v2\/prospect-findings\/search-status$",
    ]
)

#: Routes explicitly excluded from this check, each with a brief reason.
#: UUID-parameterised routes are grouped under a shared reason below.
SKIPPED_PATTERNS: dict[str, str] = {
    # Health check is HTTP-layer only; smoke tests cover it.
    r"^\/health$": "not DB-backed; HTTP layer covered by hosted smoke tests",
    # Auth / session routes resolve an operator from a Google session cookie that the
    # migrator role does not carry.
    r"^\/auth\/google\/login$": "OAuth redirect, no DB query",
    r"^\/auth\/google\/callback$": "OAuth callback, no DB query",
    r"^\/auth\/session$": "requires a signed-in operator session cookie",
    r"^\/auth\/profiles$": "requires a signed-in operator session cookie",
    # Web-publish reads need an operator session and a known model ID on disk.
    r"^\/v2\/web\/model-sheet\/[a-z0-9-]{1,60}$": "web publish; requires operator session and known model ID",
    r"^\/v2\/web\/publish-status\/[a-z0-9-]{1,60}$": "web publish; requires operator session and known model ID",
    # Marketing routes that need extra context or a different repo family.
    r"^\/v2\/workspace\/marketing\/taxonomy$": "returns static JSON from an embedded file; no DB query",
    r"^\/v2\/workspace\/marketing\/audience$": "audience inputs require a prior campaign freeze setup",
    r"^\/v2\/workspace\/marketing\/analytics\/people$": (
        "requires metric+campaign_id or segment+column query params to identify the cohort"
    ),
    r"^\/v2\/workspace\/marketing\/test-send-history$": "CampaignTestSendRepository (separate repo family)",
    # equipment-interests reads from a SQLite projection, not the Supabase schema.
    r"^\/v2\/workspace\/equipment-interests$": "reads SQLite interest projection, not the Supabase schema",
    # Routes that call external HTTP services or require live non-DB state.
    r"^\/v2\/workspace\/fx$": "calls external mindicador.cl HTTP API, not a DB read",
    r"^\/v2\/workspace\/mail-sync$": "queries Gmail mailbox authorization state; not meaningful without a live mailbox",
    r"^\/v2\/workspace\/operations\/infrastructure$": "depends on live Render API + Supabase metrics; not pure DB",
    r"^\/v2\/workspace\/drive$": "requires Google Drive OAuth session; not meaningful without a Drive connection",
    # Routes that use a different repository family not wired into the check.
    r"^\/v2\/workspace\/mail-rules\/preview$": "admin-only; MailRulesRepository (separate family)",
    r"^\/v2\/workspace\/automatic-actions$": "MailRulesRepository (separate family)",
    # Catalog routes that need live app-state objects or specific query params.
    r"^\/v2\/catalog\/fx$": "reads from catalog_fx app-state object (mindicador.cl cache), not the catalog reads repo",
    r"^\/v2\/catalog\/price-history$": "requires a known model_key= query param; returns empty without catalog data",
    r"^\/v2\/catalog\/images\/urls$": "requires up to 60 known image UUIDs",
    # Bidder profile is null until a save-bidder-profile command has been issued.
    r"^\/v2\/bidder$": "returns null profile without a prior save-bidder-profile command; not meaningful in a fresh schema",
    # merge-preview needs two specific person UUIDs in query params.
    r"^\/v2\/workspace\/people\/merge-preview$": "requires two specific person UUIDs as query params",
    # ── UUID-parameterised routes ─────────────────────────────────────────────
    # All routes with a UUID segment need an existing record UUID to be meaningful;
    # they return 404 on unknown IDs and are covered by integration tests.
    r"^\/v2\/contacts\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$": "requires existing record UUID",
    r"^\/v2\/organizations\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$": "requires existing record UUID",
    r"^\/v2\/organizations\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/cases$": "requires existing record UUID",
    r"^\/v2\/cases\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$": "requires existing record UUID",
    r"^\/v2\/workspace\/marketing\/campaigns\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$": "requires existing campaign UUID",
    r"^\/v2\/workspace\/marketing\/campaigns\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/freeze-preview$": "requires existing campaign UUID",
    r"^\/v2\/workspace\/marketing\/campaigns\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/recipients$": "requires existing campaign UUID",
    r"^\/v2\/workspace\/marketing\/campaigns\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/archive$": "requires existing campaign UUID",
    r"^\/v2\/workspace\/marketing\/campaigns\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/history\/(recipients|replies|audit)$": "requires existing campaign UUID",
    r"^\/v2\/workspace\/people\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$": "requires existing record UUID",
    r"^\/v2\/workspace\/organizations\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/authoring$": "requires existing record UUID",
    r"^\/v2\/workspace\/opportunities\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/notes$": "requires existing record UUID",
    r"^\/v2\/workspace\/opportunities\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/mail-documents$": "requires existing record UUID",
    r"^\/v2\/workspace\/opportunities\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/threads$": "requires existing record UUID",
    r"^\/v2\/workspace\/opportunities\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/messages\/[a-z0-9_-]{1,64}$": "requires existing record UUID and Gmail message ID",
    r"^\/v2\/workspace\/cases\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/memory$": "requires existing record UUID",
    r"^\/v2\/workspace\/opportunities\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/quote-candidates$": "requires existing record UUID",
    r"^\/v2\/workspace\/opportunities\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/purchase-order-candidates$": "requires existing record UUID",
    r"^\/v2\/workspace\/opportunities\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/documents\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/url$": "requires existing record UUIDs + storage",
    r"^\/v2\/tenders\/discovered\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$": "requires existing record UUID",
    r"^\/v2\/prospect-findings\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$": "requires existing record UUID",
    r"^\/v2\/prospect-findings\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/logo$": "requires existing record UUID",
    r"^\/v2\/prospect-findings\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/email-preview$": "requires existing record UUID",
    r"^\/v2\/bidder\/documents\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/url$": "requires existing record UUID + storage",
    r"^\/v2\/tenders\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$": "requires existing record UUID",
    r"^\/v2\/tenders\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/preparations\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/files\/[a-z0-9_-]{1,64}\/url$": "requires existing record UUIDs + storage",
    r"^\/v2\/cockpit\/opportunities\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/timeline$": "requires existing record UUID",
    r"^\/v2\/catalog\/products\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$": "requires existing record UUID",
    r"^\/v2\/catalog\/suppliers\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/terms$": "requires existing record UUID",
    r"^\/v2\/catalog\/images\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/url$": "requires existing record UUID + storage",
    r"^\/v2\/quotes\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$": "requires existing record UUID",
}


# -- connection helper -----------------------------------------------------------


class _OwnerConnection:
    """A psycopg connection opened as origenlab_migrator, then SET ROLEd to origenlab_owner.

    SET ROLE is session-level (not transaction-level), so it persists for the full connection
    lifetime.  `default_transaction_read_only` is set on the session too, so every transaction is
    read-only even where a repository's _read() does not say so itself.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._conn: Any = None

    def __enter__(self) -> Any:
        import psycopg  # psycopg3

        # Open with autocommit=True so SET ROLE runs outside a transaction.
        conn = psycopg.connect(self._dsn, autocommit=True)
        conn.execute("set role origenlab_owner")
        # The owner may write: every transaction on this session is read-only regardless of
        # whether a repository's own _read() says so, so no read path can change production.
        conn.execute("set default_transaction_read_only = on")
        # Switch off autocommit; the repositories then start their own transactions.
        conn.autocommit = False
        self._conn = conn
        return conn

    def __exit__(self, *exc_info: Any) -> bool:
        if self._conn is not None:
            self._conn.close()
        return False


def _make_connect(dsn: str) -> Callable[..., _OwnerConnection]:
    """Return a connect callable whose signature matches V2ConnectionPool.connect."""

    def connect(dsn_ignored: str, autocommit: bool = False) -> _OwnerConnection:  # noqa: ARG001
        return _OwnerConnection(dsn)

    return connect


# -- stub FX resolver for V2QuoteReads -------------------------------------------


def _stub_fx(currency: str, as_of: Any) -> dict[str, Any]:
    """A no-op FX resolver: 1-to-1 conversion, never calls an external API."""
    return {
        "rate": Decimal("1"),
        "clp_per_unit": Decimal("1"),
        "as_of": str(as_of),
        "source": "stub",
    }


# -- read list -------------------------------------------------------------------
# Each entry: (route_description, callable(connect, dsn) -> Any).
# The result is passed through jsonable_encoder; a serialization error is a FAIL.
# No row data is printed — production holds customer data.

_READS: list[tuple[str, Callable[..., Any]]] = [
    # ── Finance ──────────────────────────────────────────────────────────────
    (
        "GET /v2/workspace/finance/mes",
        lambda c, d: FinanceReadRepository(c, d).mes(),
    ),
    (
        "GET /v2/workspace/finance/expenses",
        lambda c, d: FinanceReadRepository(c, d).expenses(),
    ),
    (
        "GET /v2/workspace/finance/supplier-orders",
        lambda c, d: FinanceReadRepository(c, d).supplier_orders(),
    ),
    (
        "GET /v2/workspace/finance/settings",
        lambda c, d: FinanceReadRepository(c, d).settings(),
    ),
    # ── Todos / Invoices ─────────────────────────────────────────────────────
    (
        "GET /v2/workspace/todos",
        lambda c, d: TodoReadRepository(c, d).list(done_days=0),
    ),
    (
        "GET /v2/workspace/invoices",
        lambda c, d: InvoiceReadRepository(c, d).list(),
    ),
    # ── Catalog flat reads ───────────────────────────────────────────────────
    (
        "GET /v2/catalog/products",
        lambda c, d: V2CatalogReads(c, d).search_products(None, None, None, 1, 0),
    ),
    (
        "GET /v2/catalog/suppliers",
        lambda c, d: V2CatalogReads(c, d).suppliers(),
    ),
    (
        "GET /v2/catalog/parameters",
        lambda c, d: V2CatalogReads(c, d).parameters(),
    ),
    (
        "GET /v2/catalog/index",
        lambda c, d: V2CatalogReads(c, d).catalog_index(None),
    ),
    # ── Cotizador (quote) flat reads ─────────────────────────────────────────
    (
        "GET /v2/quotes/numbering",
        lambda c, d: V2QuoteReads(c, d, fx_resolver=_stub_fx).numbering(),
    ),
    (
        "GET /v2/quotes/clients",
        lambda c, d: V2QuoteReads(c, d, fx_resolver=_stub_fx).client_suggestions(q=""),
    ),
    (
        "GET /v2/quotes",
        lambda c, d: V2QuoteReads(c, d, fx_resolver=_stub_fx).quote_list(
            status="all", q=None, limit=1, cursor=None
        ),
    ),
    (
        # An id no product has: the query still runs end to end and returns None (the route's 404).
        "GET /v2/quotes/product-expenses",
        lambda c, d: V2QuoteReads(c, d, fx_resolver=_stub_fx).product_expense_history(
            UUID("00000000-0000-0000-0000-000000000000")
        ),
    ),
    (
        "GET /v2/quotes/economics",
        lambda c, d: V2QuoteReads(c, d, fx_resolver=_stub_fx).economics_report(
            from_month=None, to_month=None
        ),
    ),
    # ── Cockpit and V2 CRM flat reads ────────────────────────────────────────
    (
        "GET /v2/cockpit/work-queue",
        lambda c, d: CockpitRepository(c, d).work_queue(limit=1, offset=0),
    ),
    (
        "GET /v2/contacts",
        lambda c, d: V2Repository(c, d).contacts(q=None, limit=1, offset=0),
    ),
    (
        "GET /v2/organizations",
        lambda c, d: V2Repository(c, d).organizations(q=None, limit=1, offset=0),
    ),
    (
        "GET /v2/cases",
        lambda c, d: V2Repository(c, d).cases(stage=None, open_only=False, limit=1, offset=0),
    ),
    (
        "GET /v2/prospects",
        lambda c, d: V2Repository(c, d).prospects(limit=1, offset=0),
    ),
    (
        "GET /v2/opportunities/active",
        lambda c, d: V2Repository(c, d).active_opportunities(limit=1, offset=0),
    ),
    (
        "GET /v2/tasks/due",
        lambda c, d: V2Repository(c, d).tasks_due(horizon_days=7, limit=1, offset=0),
    ),
    (
        "GET /v2/review/summary",
        lambda c, d: V2Repository(c, d).review_summary(),
    ),
    (
        "GET /v2/quotes/followup",
        lambda c, d: V2Repository(c, d).quotes_to_follow_up(limit=1, offset=0),
    ),
    (
        "GET /v2/evidence",
        lambda c, d: V2Repository(c, d).evidence(
            q=None, resolution=None, source_kind=None, limit=1, offset=0
        ),
    ),
    (
        "GET /v2/evidence/records",
        lambda c, d: V2Repository(c, d).evidence_records(
            source_kind=None, review_status=None, limit=1, offset=0
        ),
    ),
    # ── CRM workspace flat reads ─────────────────────────────────────────────
    (
        "GET /v2/workspace/overview",
        lambda c, d: CrmWorkspaceRepository(c, d).overview(),
    ),
    (
        "GET /v2/workspace/pipeline",
        lambda c, d: CrmWorkspaceRepository(c, d).pipeline(),
    ),
    (
        "GET /v2/workspace/marketing",
        lambda c, d: CrmWorkspaceRepository(c, d).marketing(),
    ),
    (
        "GET /v2/workspace/marketing/analytics",
        lambda c, d: CrmWorkspaceRepository(c, d).marketing_analytics(),
    ),
    (
        "GET /v2/workspace/marketing/suppressions",
        lambda c, d: CrmWorkspaceRepository(c, d).suppressions(),
    ),
    (
        "GET /v2/workspace/marketing/campaign-blocks",
        lambda c, d: CrmWorkspaceRepository(c, d).campaign_blocks(),
    ),
    (
        "GET /v2/workspace/providers",
        lambda c, d: CrmWorkspaceRepository(c, d).providers(),
    ),
    (
        "GET /v2/workspace/review",
        lambda c, d: CrmWorkspaceRepository(c, d).review(),
    ),
    (
        "GET /v2/workspace/operations",
        lambda c, d: CrmWorkspaceRepository(c, d).operations(),
    ),
    (
        "GET /v2/workspace/mail-quote-numbers",
        lambda c, d: CrmWorkspaceRepository(c, d).mail_quote_numbers(),
    ),
    (
        "GET /v2/workspace/history",
        lambda c, d: CrmWorkspaceRepository(c, d).history(),
    ),
    (
        "GET /v2/workspace/quote-readings",
        lambda c, d: CrmWorkspaceRepository(c, d).quote_readings(),
    ),
    (
        "GET /v2/workspace/person-suggestions",
        lambda c, d: CrmWorkspaceRepository(c, d).person_suggestions(),
    ),
    (
        "GET /v2/workspace/lookup",
        lambda c, d: CrmWorkspaceRepository(c, d).lookup(q="origenlab"),
    ),
    # ── Triage, tenders and prospects ────────────────────────────────────────
    (
        "GET /v2/workspace/triage-readings",
        lambda c, d: TriageReviewRepository(c, d).readings(limit=1),
    ),
    (
        "GET /v2/workspace/forwarded-requests",
        lambda c, d: TriageReviewRepository(c, d).forwarded_requests(),
    ),
    (
        "GET /v2/tenders",
        lambda c, d: TenderReadRepository(c, d).list_tenders(),
    ),
    (
        "GET /v2/tenders/discovered",
        lambda c, d: TenderReadRepository(c, d).discovered(now=datetime.now(timezone.utc)),
    ),
    (
        "GET /v2/prospect-findings",
        lambda c, d: ProspectFindingReadRepository(c, d).list(view="all", limit=1, offset=0),
    ),
    (
        "GET /v2/prospect-findings/search-status",
        lambda c, d: ProspectFindingReadRepository(c, d).search_status(),
    ),
]


# -- check runner ----------------------------------------------------------------


def run_checks(dsn: str) -> int:
    """Call each listed read once, apply jsonable_encoder, and report ok/FAIL.

    Catches code, SQL and serialization bugs (such as Decimal × float, #891/#893) for every
    route in CHECKED_PATTERNS.  Does NOT cover grant bugs — see module docstring.

    Prints one line per read: ``ok  <route>`` or ``FAIL  <route>  ExcType: short message``.
    Never prints row data.  Returns 0 if all reads succeed, 1 if any FAIL.
    """
    connect = _make_connect(dsn)
    failures = 0
    for name, reader in _READS:
        try:
            result = reader(connect, dsn)
            jsonable_encoder(result)
            print(f"ok  {name}", flush=True)
        except Exception as exc:  # noqa: BLE001
            short = str(exc).split("\n")[0][:120]
            print(f"FAIL  {name}  {type(exc).__name__}: {short}", flush=True)
            failures += 1
    return 1 if failures else 0


# -- production entry point ------------------------------------------------------


def main() -> None:
    """Read credentials from the environment (GitHub Actions only) and run all checks."""
    # Import lazily: hosted_env.py requires GITHUB_ACTIONS=true and cannot be used locally.
    sys.path.insert(0, str(HOSTED_ENV_DIR))
    from hosted_env import Refused, database_env  # type: ignore[import-not-found]

    with tempfile.TemporaryDirectory(
        prefix="ol-read-check-", dir=os.environ.get("RUNNER_TEMP")
    ) as cert_dir:
        env = database_env(Path(cert_dir))

        # Reconstruct a libpq connection string from the PG* env vars.  The reads run inside
        # the block: sslrootcert points into cert_dir, which is deleted when the block exits.
        dsn = (
            "host={PGHOST} port={PGPORT} dbname={PGDATABASE} user={PGUSER}"
            " password={PGPASSWORD} sslmode={PGSSLMODE} sslrootcert={PGSSLROOTCERT}"
            " connect_timeout={PGCONNECT_TIMEOUT} gssencmode={PGGSSENCMODE}"
        ).format(**env)

        code = run_checks(dsn)
    if code != 0:
        raise Refused("one or more reads FAILED — see output above")
    print(
        f"READ CHECK PASSED: {len(_READS)} routes ok, {len(SKIPPED_PATTERNS)} explicitly skipped",
        flush=True,
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print(f"REFUSED: {exc}", file=sys.stderr)
        sys.exit(1)
