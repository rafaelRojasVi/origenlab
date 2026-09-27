"""The V2 durable read boundary — `/v2/*`.

Five endpoints the operator asked for, plus the two the four CRM cards need:

| Route | Reads |
|---|---|
| `GET /v2/contacts` | `crm.contact_point` + `crm.person` |
| `GET /v2/organizations` | `crm.organization`, most connected first, with case/interest/quote/activity counts |
| `GET /v2/prospects` | `crm.opportunity` at `lead` / `qualifying` |
| `GET /v2/opportunities/active` | `crm.opportunity` not closed |
| `GET /v2/tasks/due` | `crm.task` open and due |
| `GET /v2/review/summary` | the operator review queue |
| `GET /v2/quotes/followup` | sent quote revisions not yet superseded |
| `GET /v2/contacts/{id}` | one channel, its identity, its evidence, its marketing history, and the cases its participant rows reach |
| `GET /v2/organizations/{id}` | one organization, its channels, people, domains, evidence, marketing controls, and its cases with their interests, quotes and activities |
| `GET /v2/organizations/{id}/cases` | every case that institution is part of, with the part it holds on each |
| `GET /v2/evidence` | the evidence trail, each row carrying its own provenance |
| `GET /v2/evidence/records` | the same trail grouped by source record, with its `crm.*` matches |
| `GET /v2/cases` | commercial cases — `crm.opportunity` with its institutions, interests and evidence counted |
| `GET /v2/cases/{id}` | one case: its parts, what it seeks, why it believes it, and the stage machine |

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

from origenlab_api.v2.case_commands import (
    CASE_STAGES,
    STAGE_TRANSITIONS,
    STAGES_REQUIRING_A_CLOSE_REASON,
    STAGES_REQUIRING_A_REQUESTING_INSTITUTION,
    TERMINAL_STAGES,
)
from origenlab_api.v2.contact_redaction import (
    ContactRedactingRoute,
    remember_operator,
    sees_contact_addresses,
)
from origenlab_api.v2.identity import (
    IdentityPort,
    IdentityRefused,
    OperatorIdentity,
)
from origenlab_api.v2.repository import V2Repository, clamp_limit

# Every answer is masked for an operator who may not see contact addresses
# (`contact_redaction.py`): the route class does it, so no read route can forget.
router = APIRouter(prefix="/v2", tags=["v2"], route_class=ContactRedactingRoute)


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
        operator = port.resolve(dict(request.headers)).require_active().require_role(
            "viewer", "sales", "admin"
        )
    except IdentityRefused as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    # Recorded for the route class, which masks contact addresses by role.
    return remember_operator(request, operator)


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


def _uuid_query_param(raw: str | None, name: str) -> str | None:
    """A malformed filter id is a 422: unlike a path, the resource itself was not named."""
    if raw is None:
        return None
    try:
        return str(uuid.UUID(raw))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{name} must be a UUID") from None


def _page_response(page: Any) -> dict[str, Any]:
    response = {
        "items": page.items,
        "total": page.total,
        "limit": page.limit,
        "offset": page.offset,
    }
    if getattr(page, "facets", None) is not None:
        response["facets"] = page.facets
    return response


@router.get("/contacts")
def list_contacts(
    operator: Operator,
    repo: Repo,
    q: str | None = Query(default=None, max_length=200),
    identity: str | None = Query(default=None),
    with_cases: bool = Query(default=False),
    limit: int | None = Query(default=None, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Contact points, recorded identities first.

    `q` matches the recorded person's name or the recorded institution's name, and for an
    operator who may read contact addresses (`sales`, `admin`) the address as well. A
    `viewer` gets masked addresses, so their search must not reach the address either: a
    masked hit for `persona@` would confirm what the mask hides. `identity` is `person`,
    `organization_mailbox` or `unattributed` — read from the channel's own foreign keys,
    never from its address. `with_cases` keeps channels a current participant row connects
    to a case.
    """
    if identity is not None and identity not in repo.CONTACT_IDENTITIES:
        raise HTTPException(
            status_code=422,
            detail=f"identity must be one of {', '.join(repo.CONTACT_IDENTITIES)}",
        )
    return _page_response(
        repo.contacts(
            q=q,
            identity=identity,
            with_cases=with_cases,
            search_addresses=sees_contact_addresses(operator.role),
            limit=clamp_limit(limit),
            offset=offset,
        )
    )


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
    has: list[str] = Query(default=[]),
    active_within_days: int | None = Query(default=None, ge=1, le=3650),
    segment: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Organizations, most connected first.

    `segment` keeps one commercial side — `customers` (asks OrigenLab for equipment on a
    case, or a recorded customer), `suppliers` (supplier or manufacturer on a case, or
    recorded as one) or `others` (end user, purchasing agent, funder or mentioned). Omitted
    means every organization. `facets` in the response counts each segment under the same
    filters, so the tabs can say how many rows each holds.

    `has` may repeat and names what every row must have — `contacts`, `people`, `cases`,
    `open_cases`, `interests`, `quotes`. `active_within_days` keeps organizations whose
    latest case activity is inside the window. An unknown `has` value is a 422, not an empty
    page that reads like "no institution has that".
    """
    unknown = [name for name in has if name not in repo.ORGANIZATION_FILTERS]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"has must be among {', '.join(repo.ORGANIZATION_FILTERS)}",
        )
    if segment is not None and segment not in repo.ORGANIZATION_SEGMENTS:
        raise HTTPException(
            status_code=422,
            detail=f"segment must be one of {', '.join(repo.ORGANIZATION_SEGMENTS)}",
        )
    return _page_response(
        repo.organizations(
            q=q,
            having=tuple(dict.fromkeys(has)),
            active_within_days=active_within_days,
            segment=segment,
            limit=clamp_limit(limit),
            offset=offset,
        )
    )


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


@router.get("/organizations/{organization_id}/cases")
def organization_cases(
    _: Operator,
    repo: Repo,
    organization_id: str,
    limit: int | None = Query(default=None, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Every case this institution is part of, with the part it holds on each.

    `GET /v2/cases` cannot answer this. It pages every case and carries only the
    requesting institution, so crossing it in a browser finds an organization's cases
    only where that organization is the one asking — a supplier or a manufacturer
    reads as uninvolved in the very deals it is named on. This route reads
    `crm.opportunity_organization` directly, so participation is a row rather than a
    guess, and it returns the **roles**, plural: supplier and manufacturer on the same
    case is the ordinary shape.

    A missing organization is 404, not an empty page. "No such institution" and "this
    institution is in no cases" are different answers and must not look alike.
    """
    page = repo.organization_cases(
        _uuid_path_param(organization_id, "organization"),
        limit=clamp_limit(limit),
        offset=offset,
    )
    if page is None:
        raise HTTPException(status_code=404, detail="no such organization")
    return _page_response(page)


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
    operator: Operator,
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

    `q` searches every assertion value for an operator who may read contact addresses
    (`sales`, `admin`). A `viewer` gets masked addresses, so their search must not reach the
    address kinds either: a masked hit for `persona@` would confirm what the mask hides.
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
            search_addresses=sees_contact_addresses(operator.role),
            limit=clamp_limit(limit),
            offset=offset,
        )
    )


@router.get("/cases")
def list_cases(
    _: Operator,
    repo: Repo,
    stage: list[str] = Query(default=[]),
    open_only: bool = Query(default=False),
    organization_id: str | None = Query(default=None),
    organization_q: str | None = Query(default=None, max_length=200),
    interest_q: str | None = Query(default=None, max_length=200),
    quote_state: str | None = Query(default=None),
    active_within_days: int | None = Query(default=None, ge=1, le=3650),
    limit: int | None = Query(default=None, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Commercial cases — `crm.opportunity` read as the case it is.

    `/v2/opportunities/active` and `/v2/prospects` already page the same table by stage,
    and they stay: they answer "what is in play" and this answers "what does this case
    say". A case row here carries the institution that is asking, every institution with
    the part it holds, what the case is seeking, its quote state, its latest activity and
    how much evidence stands behind it.

    `stage` may repeat (`stage=lead&stage=qualifying` is the prospect view) and is validated
    against the schema's own vocabulary, as is `quote_state`, so a typo is a 422 rather than
    an empty page that reads like "there are no cases". `organization_id` keeps cases where
    that institution holds any current part; `organization_q` and `interest_q` are searches
    over recorded part rows and non-withdrawn interests.
    """
    unknown = [value for value in stage if value not in CASE_STAGES]
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"stage must be one of {', '.join(CASE_STAGES)}"
        )
    if quote_state is not None and quote_state not in repo.QUOTE_STATES:
        raise HTTPException(
            status_code=422,
            detail=f"quote_state must be one of {', '.join(repo.QUOTE_STATES)}",
        )
    return _page_response(
        repo.cases(
            stage=tuple(dict.fromkeys(stage)),
            open_only=open_only,
            organization_id=_uuid_query_param(organization_id, "organization_id"),
            organization_q=organization_q or None,
            interest_q=interest_q or None,
            quote_state=quote_state,
            active_within_days=active_within_days,
            limit=clamp_limit(limit),
            offset=offset,
        )
    )


@router.get("/cases/{opportunity_id}")
def case_card(
    _: Operator,
    repo: Repo,
    opportunity_id: str,
) -> dict[str, Any]:
    """One commercial case, with the stage machine that governs it attached.

    `stage_machine` is not a hint and not a recommendation: it is
    `origenlab_api.v2.case_commands.STAGE_TRANSITIONS`, the same table the command boundary
    refuses by and the same one `crm.opportunity_stage_transition_allowed` enforces in the
    database. Serving it here means the dashboard can show which moves exist without
    holding a third copy of the rule that could disagree with the other two in silence.

    It says what is *reachable*, never what should happen. Whether a reachable move is
    currently permitted also depends on rows — a requesting institution from `qualified`
    on, a motive for `lost` and `abandoned` — so those conditions are named beside it
    rather than folded into the list.
    """
    card = repo.case_card(_uuid_path_param(opportunity_id, "case"))
    if card is None:
        raise HTTPException(status_code=404, detail="no such case")
    stage = str(card["stage"])
    card["stage_machine"] = {
        "stage": stage,
        "allowed_next_stages": list(STAGE_TRANSITIONS.get(stage, ())),
        "is_terminal": stage in TERMINAL_STAGES,
        "stages_requiring_a_requesting_institution": list(
            STAGES_REQUIRING_A_REQUESTING_INSTITUTION
        ),
        "stages_requiring_a_close_reason": list(STAGES_REQUIRING_A_CLOSE_REASON),
    }
    return card
