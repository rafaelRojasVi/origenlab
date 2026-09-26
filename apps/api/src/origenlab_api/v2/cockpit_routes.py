"""Operator cockpit read routes — ``/v2/cockpit/*``.

GET only.  No writes of any kind.  Every route requires an active operator
identity (``viewer`` role or above), identical to the existing ``/v2/*``
boundary.

The cockpit router is a sibling of ``routes.router`` and is mounted in
``_mount_v2_read_boundary`` alongside it, sharing the same
``V2Repository``-based identity and read infrastructure.  It adds a separate
``CockpitRepository`` that borrows the same DSN, so the isolation guarantee
(``begin read only``, ``origenlab_api`` role) is identical.

**Proxy allowlist** — the following GET paths must be added to
``apps/dashboard-proxy``'s method+path allowlist before the dashboard can
reach them:

    GET /v2/cockpit/kpis
    GET /v2/cockpit/work-queue
    GET /v2/cockpit/opportunities
    GET /v2/cockpit/opportunities/{id}
    GET /v2/cockpit/opportunities/{id}/timeline
    GET /v2/cockpit/quotations
    GET /v2/cockpit/quotations/{quote_id}
    GET /v2/cockpit/evidence/{source_record_id}
    GET /v2/cockpit/search
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from origenlab_api.v2.cockpit_repository import CockpitRepository
from origenlab_api.v2.cockpit_schemas import (
    EvidenceDrawer,
    KpiResponse,
    OpportunityDetail,
    OpportunityListResponse,
    QuotationDetail,
    QuotationListResponse,
    SearchResponse,
    TimelineResponse,
    WorkQueueResponse,
)
from origenlab_api.v2.contact_redaction import ContactRedactingRoute, remember_operator
from origenlab_api.v2.identity import IdentityPort, IdentityRefused, OperatorIdentity
from origenlab_api.v2.repository import clamp_limit

cockpit_router = APIRouter(prefix="/v2/cockpit", tags=["cockpit"], route_class=ContactRedactingRoute)

# Maximum page size and default accepted for the cockpit, same as the rest of /v2.
_MAX_SEARCH_Q = 200


def _get_cockpit_repository(request: Request) -> CockpitRepository:
    repo = getattr(request.app.state, "cockpit_repository", None)
    if repo is None:  # pragma: no cover
        raise HTTPException(status_code=503, detail="cockpit repository is not configured")
    return repo


def _get_identity_port(request: Request) -> IdentityPort:
    port = getattr(request.app.state, "v2_identity", None)
    if port is None:  # pragma: no cover
        raise HTTPException(status_code=503, detail="the V2 read boundary is not configured")
    return port


def _current_operator(
    request: Request,
    port: IdentityPort = Depends(_get_identity_port),
) -> OperatorIdentity:
    try:
        operator = port.resolve(dict(request.headers)).require_active().require_role(
            "viewer", "sales", "admin"
        )
    except IdentityRefused as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    # Recorded for the route class, which masks contact addresses by role.
    return remember_operator(request, operator)


Operator = Annotated[OperatorIdentity, Depends(_current_operator)]
CockpitRepo = Annotated[CockpitRepository, Depends(_get_cockpit_repository)]


def _uuid_path(raw: str, what: str) -> str:
    try:
        return str(uuid.UUID(raw))
    except ValueError:
        raise HTTPException(status_code=404, detail=f"no such {what}") from None


def _uuid_query(raw: str | None, name: str) -> str | None:
    if raw is None:
        return None
    try:
        return str(uuid.UUID(raw))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{name} must be a UUID") from None


# ─────────────────────────────────────────────────────────────── GET /kpis ──


@cockpit_router.get("/kpis", response_model=KpiResponse)
def get_kpis(_: Operator, repo: CockpitRepo) -> Any:
    """All cockpit KPI counters in one round-trip."""
    return repo.kpis()


# ──────────────────────────────────────────────────── GET /work-queue ───────


@cockpit_router.get("/work-queue", response_model=WorkQueueResponse)
def get_work_queue(
    _: Operator,
    repo: CockpitRepo,
    limit: int | None = Query(default=None, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Any:
    """Typed actionable work items, ordered oldest-first within kind."""
    return repo.work_queue(limit=clamp_limit(limit), offset=offset)


# ────────────────────────────────────────────────── GET /opportunities ──────


_VALID_STAGES = frozenset(
    ("lead", "qualifying", "qualified", "quoting", "negotiating", "won", "lost", "abandoned")
)


@cockpit_router.get("/opportunities", response_model=OpportunityListResponse)
def list_opportunities(
    _: Operator,
    repo: CockpitRepo,
    stage: str | None = Query(default=None),
    organization_id: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=_MAX_SEARCH_Q),
    has_quote: bool | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Any:
    """Paginated opportunity list with counts.

    ``stage`` is validated against the closed vocabulary.
    ``organization_id`` must be a valid UUID when provided.
    ``has_quote`` filters to opportunities that do (True) or do not (False) have
    at least one quote.
    """
    if stage is not None and stage not in _VALID_STAGES:
        raise HTTPException(
            status_code=422,
            detail=f"stage must be one of {', '.join(sorted(_VALID_STAGES))}",
        )
    return repo.opportunities(
        stage=stage,
        organization_id=_uuid_query(organization_id, "organization_id"),
        q=q or None,
        has_quote=has_quote,
        date_from=date_from or None,
        date_to=date_to or None,
        limit=clamp_limit(limit),
        offset=offset,
    )


@cockpit_router.get("/opportunities/{opportunity_id}", response_model=OpportunityDetail)
def get_opportunity(
    _: Operator,
    repo: CockpitRepo,
    opportunity_id: str,
) -> Any:
    """Full opportunity card with quotes, revisions, evidence links and completeness flags."""
    uid = _uuid_path(opportunity_id, "opportunity")
    detail = repo.opportunity_detail(uid)
    if detail is None:
        raise HTTPException(status_code=404, detail="no such opportunity")
    return detail


# ─────────────────────────────────────────── GET /opportunities/{id}/timeline


@cockpit_router.get("/opportunities/{opportunity_id}/timeline", response_model=TimelineResponse)
def get_opportunity_timeline(
    _: Operator,
    repo: CockpitRepo,
    opportunity_id: str,
) -> Any:
    """Ordered event timeline for one opportunity and all its CRM aggregates."""
    uid = _uuid_path(opportunity_id, "opportunity")
    timeline = repo.opportunity_timeline(uid)
    if timeline is None:
        raise HTTPException(status_code=404, detail="no such opportunity")
    return timeline


# ──────────────────────────────────────────────────── GET /quotations ───────

_VALID_STATUSES = frozenset(("draft", "in_review", "approved", "sent", "void"))
_VALID_ORIGINS = frozenset(("minted", "printed_historical"))


@cockpit_router.get("/quotations", response_model=QuotationListResponse)
def list_quotations(
    _: Operator,
    repo: CockpitRepo,
    quote_number: str | None = Query(default=None, max_length=64),
    organization_id: str | None = Query(default=None),
    opportunity_id: str | None = Query(default=None),
    gmail_message_id: str | None = Query(default=None, max_length=256),
    status: str | None = Query(default=None),
    number_origin: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Any:
    """Paginated quotation list.

    ``status`` filters quotes that have at least one revision with that status.
    ``quote_number`` is an exact or prefix match.
    ``number_origin`` is ``minted`` or ``printed_historical``.
    """
    if status is not None and status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of {', '.join(sorted(_VALID_STATUSES))}",
        )
    if number_origin is not None and number_origin not in _VALID_ORIGINS:
        raise HTTPException(
            status_code=422,
            detail=f"number_origin must be one of {', '.join(sorted(_VALID_ORIGINS))}",
        )
    return repo.quotations(
        quote_number=quote_number or None,
        organization_id=_uuid_query(organization_id, "organization_id"),
        opportunity_id=_uuid_query(opportunity_id, "opportunity_id"),
        gmail_message_id=gmail_message_id or None,
        status=status or None,
        number_origin=number_origin or None,
        date_from=date_from or None,
        date_to=date_to or None,
        limit=clamp_limit(limit),
        offset=offset,
    )


@cockpit_router.get("/quotations/{quote_id}", response_model=QuotationDetail)
def get_quotation(
    _: Operator,
    repo: CockpitRepo,
    quote_id: str,
) -> Any:
    """Full quotation card with revisions, Gmail sources and conflict flags."""
    uid = _uuid_path(quote_id, "quotation")
    detail = repo.quotation_detail(uid)
    if detail is None:
        raise HTTPException(status_code=404, detail="no such quotation")
    return detail


# ───────────────────────────────────────────────── GET /evidence/{id} ───────


@cockpit_router.get("/evidence/{source_record_id}", response_model=EvidenceDrawer)
def get_evidence_drawer(
    _: Operator,
    repo: CockpitRepo,
    source_record_id: str,
) -> Any:
    """Evidence drawer for one source record — assertions, Gmail metadata, audit events.

    Never returns the raw email body.
    """
    uid = _uuid_path(source_record_id, "source_record")
    drawer = repo.evidence_drawer(uid)
    if drawer is None:
        raise HTTPException(status_code=404, detail="no such source record")
    return drawer


# ──────────────────────────────────────────────────────── GET /search ───────


@cockpit_router.get("/search", response_model=SearchResponse)
def cockpit_search(
    _: Operator,
    repo: CockpitRepo,
    q: str = Query(min_length=1, max_length=_MAX_SEARCH_Q),
) -> Any:
    """Exact / prefix search across quote numbers, Gmail ids, sha256, orgs, titles.

    No fuzzy matching.  sha256 prefix search requires at least 12 hex characters.
    """
    return repo.search(q)
