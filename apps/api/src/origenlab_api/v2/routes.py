"""The V2 durable read boundary — `/v2/*`.

Five endpoints the operator asked for, plus the two the four CRM cards need:

| Route | Reads |
|---|---|
| `GET /v2/contacts` | `crm.contact_point` + `crm.person` |
| `GET /v2/organizations` | `crm.organization` |
| `GET /v2/prospects` | `crm.opportunity` at `lead` / `qualifying` |
| `GET /v2/opportunities/active` | `crm.opportunity` not closed |
| `GET /v2/tasks/due` | `crm.task` open and due |
| `GET /v2/review/summary` | the operator review queue |
| `GET /v2/quotes/followup` | sent quote revisions not yet superseded |
| `GET /v2/contacts/{id}` | one channel, its identity, its evidence and its marketing history |
| `GET /v2/organizations/{id}` | one organization, its channels, people, domains and evidence |
| `GET /v2/evidence` | the evidence trail, each row carrying its own provenance |
| `GET /v2/evidence/records` | the same trail grouped by source record, with its `crm.*` matches |

**Read-only, and structurally so.** Every query runs in `begin read only` as `origenlab_api`,
a role with no membership in `origenlab_owner`. There is no POST, PATCH or DELETE here and
there must not be: durable writes belong to the command boundary of `docs/ARCHITECTURE.md`,
not to a read router.

**The router is not mounted unless `ORIGENLAB_V2_DATABASE_URL` is set.** An unconfigured
deployment gets no `/v2/*` surface at all rather than a surface that errors, so nothing
about the running V1 API changes until the V2 database is deliberately configured.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from origenlab_api.v2.identity import (
    IdentityPort,
    IdentityRefused,
    OperatorIdentity,
)
from origenlab_api.v2.repository import V2Repository, clamp_limit

router = APIRouter(prefix="/v2", tags=["v2"])


def get_repository(request: Request) -> V2Repository:
    repo = getattr(request.app.state, "v2_repository", None)
    if repo is None:  # pragma: no cover - the router is not mounted without one
        raise HTTPException(status_code=503, detail="the V2 read boundary is not configured")
    return repo


def get_identity_port(request: Request) -> IdentityPort:
    port = getattr(request.app.state, "v2_identity", None)
    if port is None:  # pragma: no cover - the router is not mounted without one
        raise HTTPException(status_code=503, detail="the V2 read boundary is not configured")
    return port


def current_operator(
    request: Request,
    port: IdentityPort = Depends(get_identity_port),
) -> OperatorIdentity:
    """Resolve the operator, or refuse with 401.

    Every `/v2` read requires a resolved, active operator. A read boundary over real contact
    data is not a public surface, and `viewer` is the weakest role that may see any of it
    (`docs/OPERATIONS.md` §2).
    """
    try:
        return port.resolve(dict(request.headers)).require_active().require_role(
            "viewer", "sales", "admin"
        )
    except IdentityRefused as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


Operator = Annotated[OperatorIdentity, Depends(current_operator)]
Repo = Annotated[V2Repository, Depends(get_repository)]


def _uuid_path_param(raw: str, what: str) -> str:
    """Refuse a malformed identifier at the boundary, before it reaches a query.

    Without this a typo in a URL becomes an invalid-input SQLSTATE from the driver and a
    500. A path segment that is not a UUID names nothing, so 404 is the honest answer.
    """
    try:
        return str(uuid.UUID(raw))
    except ValueError:
        raise HTTPException(status_code=404, detail=f"no such {what}") from None


def _page_response(page: Any) -> dict[str, Any]:
    return {
        "items": page.items,
        "total": page.total,
        "limit": page.limit,
        "offset": page.offset,
    }


@router.get("/contacts")
def list_contacts(
    _: Operator,
    repo: Repo,
    q: str | None = Query(default=None, max_length=200),
    limit: int | None = Query(default=None, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return _page_response(repo.contacts(q=q, limit=clamp_limit(limit), offset=offset))


@router.get("/contacts/{contact_point_id}")
def contact_card(
    _: Operator,
    repo: Repo,
    contact_point_id: str,
) -> dict[str, Any]:
    """One contact card.

    A channel whose owner is unknown is the common case in the migrated data, so the card
    reports `person`, `organization`, `affiliations` and `evidence` as separately-empty
    facts rather than flattening them into a row of nulls.
    """
    card = repo.contact_card(_uuid_path_param(contact_point_id, "contact"))
    if card is None:
        raise HTTPException(status_code=404, detail="no such contact")
    return card


@router.get("/organizations")
def list_organizations(
    _: Operator,
    repo: Repo,
    q: str | None = Query(default=None, max_length=200),
    limit: int | None = Query(default=None, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return _page_response(repo.organizations(q=q, limit=clamp_limit(limit), offset=offset))


@router.get("/organizations/{organization_id}")
def organization_card(
    _: Operator,
    repo: Repo,
    organization_id: str,
) -> dict[str, Any]:
    """One organization card, with its relationships and its provenance."""
    card = repo.organization_card(_uuid_path_param(organization_id, "organization"))
    if card is None:
        raise HTTPException(status_code=404, detail="no such organization")
    return card


@router.get("/prospects")
def list_prospects(
    _: Operator,
    repo: Repo,
    limit: int | None = Query(default=None, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Prospects and leads.

    `lead` is a stage, not an entity (`docs/DOMAIN.md` §3.2), so this is `crm.opportunity`
    filtered by stage. There is no prospect table and there must not be one.
    """
    return _page_response(repo.prospects(limit=clamp_limit(limit), offset=offset))


@router.get("/opportunities/active")
def list_active_opportunities(
    _: Operator,
    repo: Repo,
    limit: int | None = Query(default=None, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return _page_response(
        repo.active_opportunities(limit=clamp_limit(limit), offset=offset)
    )


@router.get("/tasks/due")
def list_tasks_due(
    _: Operator,
    repo: Repo,
    horizon_days: int = Query(default=7, ge=0, le=365),
    limit: int | None = Query(default=None, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Follow-ups due. `horizon_days = 0` returns only what is already overdue."""
    return _page_response(
        repo.tasks_due(horizon_days=horizon_days, limit=clamp_limit(limit), offset=offset)
    )


@router.get("/review/summary")
def review_summary(_: Operator, repo: Repo) -> dict[str, int]:
    """What a human still has to decide, counted by why.

    This is the only one of these endpoints that is fully populated today: the migration
    promoted identities conservatively and left everything it could not establish here.
    """
    return repo.review_summary()


@router.get("/quotes/followup")
def list_quotes_to_follow_up(
    _: Operator,
    repo: Repo,
    limit: int | None = Query(default=None, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return _page_response(repo.quotes_to_follow_up(limit=clamp_limit(limit), offset=offset))


@router.get("/evidence/records")
def list_evidence_records(
    _: Operator,
    repo: Repo,
    source_kind: str | None = Query(default=None),
    review_status: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """The review queue as a human reads it — one row per source record.

    `/v2/evidence` is assertion-shaped and answers "where did this fact come from".
    A reviewer asks the other question: "what am I being asked to decide about this
    message". That is this route. Each row carries the record, its assertions, and the
    `crm.*` rows those assertions already match — an existing address, an identically named
    organization, an organization reached through a registered domain.

    It proposes nothing. A match here is a fact about what already exists, never a
    recommendation to promote, and the route stays a GET for the same reason the rest of
    `/v2` does.
    """
    if source_kind is not None and source_kind not in repo.EVIDENCE_SOURCE_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"source_kind must be one of {', '.join(repo.EVIDENCE_SOURCE_KINDS)}",
        )
    if review_status is not None and review_status not in repo.EVIDENCE_REVIEW_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"review_status must be one of {', '.join(repo.EVIDENCE_REVIEW_STATUSES)}",
        )
    return _page_response(
        repo.evidence_records(
            source_kind=source_kind,
            review_status=review_status,
            limit=clamp_limit(limit),
            offset=offset,
        )
    )


@router.get("/evidence")
def list_evidence(
    _: Operator,
    repo: Repo,
    q: str | None = Query(default=None, max_length=200),
    resolution: str | None = Query(default=None),
    source_kind: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """The evidence trail — the review queue in list form.

    `/v2/review/summary` counts what is waiting; this says which rows they are. Both
    filters are validated against the database's own closed vocabularies, so a value the
    schema cannot hold is a 422 rather than an empty page that looks like an answer.
    """
    if resolution is not None and resolution not in repo.EVIDENCE_RESOLUTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"resolution must be one of {', '.join(repo.EVIDENCE_RESOLUTIONS)}",
        )
    if source_kind is not None and source_kind not in repo.EVIDENCE_SOURCE_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"source_kind must be one of {', '.join(repo.EVIDENCE_SOURCE_KINDS)}",
        )
    return _page_response(
        repo.evidence(
            q=q,
            resolution=resolution,
            source_kind=source_kind,
            limit=clamp_limit(limit),
            offset=offset,
        )
    )
