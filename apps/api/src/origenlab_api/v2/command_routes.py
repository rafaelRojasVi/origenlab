"""The V2 human-review command boundary — `POST /v2/commands/*`.

| Route | Records |
|---|---|
| `POST /v2/commands/keep-evidence-pending` | an operator read this record and left it pending |
| `POST /v2/commands/confirm-organization` | an asserted name is one existing organization |
| `POST /v2/commands/create-organization` | an asserted name is an organization nobody had |
| `POST /v2/commands/attach-contact-address` | an asserted address is an organization's mailbox |

**Why this is a second router.** `routes.py` is the read boundary, and a test over that
router's own methods asserts it has no POST, PATCH or DELETE. That assertion is worth
keeping true, so the write path lives here instead of being appended there: the read surface
stays provably read-only, and "does `/v2` write?" has a one-file answer.

**Four requirements, on every route, before anything runs.** An active operator whose role is
`sales` or `admin`; an `Idempotency-Key` header; a `source_record_id` naming the evidence the
decision is about; and a non-blank `note` saying why. Three of the four are in the request and
the fourth is not — the operator comes from the verified identity, never from the body, so a
caller cannot decide *as* somebody else.

**The dashboard cannot reach these routes yet, deliberately.** `apps/dashboard-proxy` allows
no POST under `/v2` and this change does not open it. The command boundary and the decision
to let a browser through it are separate steps, and a test in the Worker's suite keeps the
second one from happening by accident.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from origenlab_api.v2.command_repository import V2CommandRepository
from origenlab_api.v2.commands import (
    ATTACH_CONTACT_ADDRESS,
    CONFIRM_ORGANIZATION,
    CREATE_ORGANIZATION,
    KEEP_EVIDENCE_PENDING,
    AttachContactAddressBody,
    CommandRefused,
    ConfirmOrganizationBody,
    CreateOrganizationBody,
    KeepEvidencePendingBody,
    request_digest,
    require_idempotency_key,
    require_writer,
    validated,
)
from origenlab_api.v2.identity import IdentityPort, IdentityRefused, OperatorIdentity

command_router = APIRouter(prefix="/v2/commands", tags=["v2-commands"])


def get_command_repository(request: Request) -> V2CommandRepository:
    repo = getattr(request.app.state, "v2_command_repository", None)
    if repo is None:  # pragma: no cover - the router is not mounted without one
        raise HTTPException(status_code=503, detail="the V2 command boundary is not configured")
    return repo


def get_identity_port(request: Request) -> IdentityPort:
    port = getattr(request.app.state, "v2_identity", None)
    if port is None:  # pragma: no cover - the router is not mounted without one
        raise HTTPException(status_code=503, detail="the V2 command boundary is not configured")
    return port


def deciding_operator(
    request: Request,
    port: IdentityPort = Depends(get_identity_port),
) -> OperatorIdentity:
    """Resolve the operator and check they may decide, in that order.

    The order is the whole point. An unresolved caller is 401 — we do not know who they are.
    A resolved `viewer` is 403 — we know exactly who they are, and reading the queue is not
    deciding it (`docs/OPERATIONS.md` §2).
    """
    try:
        identity = port.resolve(dict(request.headers)).require_active()
    except IdentityRefused as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    try:
        require_writer(identity.role)
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc
    return identity


Deciding = Annotated[OperatorIdentity, Depends(deciding_operator)]
CommandRepo = Annotated[V2CommandRepository, Depends(get_command_repository)]
IdempotencyKey = Annotated[str | None, Header(alias="Idempotency-Key")]


def _detail(exc: CommandRefused) -> dict[str, str]:
    return {"code": exc.code, "message": exc.message}


def _run(
    *,
    command_name: str,
    body: Any,
    repo: V2CommandRepository,
    operator: OperatorIdentity,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """The same six steps for every command, so no route can quietly skip one."""
    try:
        key = require_idempotency_key(idempotency_key)
        fields = validated(command_name, body)
        digest = request_digest(command_name, body)
        return repo.execute(
            command_name=command_name,
            operator=operator,
            fields=fields,
            idempotency_key=key,
            digest=digest,
        )
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc


@command_router.post("/keep-evidence-pending")
def keep_evidence_pending(
    body: KeepEvidencePendingBody,
    operator: Deciding,
    repo: CommandRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Record that a named operator read this record and left it pending.

    Nothing in `crm.*` moves and `review_status` stays `'pending'`, because the record still
    is. What changes is that "nobody has looked at this" and "somebody looked and the
    evidence was not enough" stop being the same database state.
    """
    return _run(
        command_name=KEEP_EVIDENCE_PENDING,
        body=body,
        repo=repo,
        operator=operator,
        idempotency_key=idempotency_key,
    )


@command_router.post("/confirm-organization")
def confirm_organization(
    body: ConfirmOrganizationBody,
    operator: Deciding,
    repo: CommandRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Confirm that an asserted organization name is one specific existing organization.

    The organization is named by id and checked by exact folded name. A near miss is refused
    with `organization_name_is_not_an_exact_match` rather than accepted with a caveat: the
    operator can still create the other organization, or merge them somewhere this boundary
    deliberately does not reach.
    """
    return _run(
        command_name=CONFIRM_ORGANIZATION,
        body=body,
        repo=repo,
        operator=operator,
        idempotency_key=idempotency_key,
    )


@command_router.post("/create-organization")
def create_organization(
    body: CreateOrganizationBody,
    operator: Deciding,
    repo: CommandRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Create an organization the evidence names and the CRM does not have.

    The name is read from the assertion, never from the request, so this route cannot create
    an organization out of a sender domain, a subject line or an operator's typing. If the
    name already exists the command refuses and points at `confirm-organization`.
    """
    return _run(
        command_name=CREATE_ORGANIZATION,
        body=body,
        repo=repo,
        operator=operator,
        idempotency_key=idempotency_key,
    )


@command_router.post("/attach-contact-address")
def attach_contact_address(
    body: AttachContactAddressBody,
    operator: Deciding,
    repo: CommandRepo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Attach an asserted address to an organization as a mailbox that organization operates.

    `usage` must be `shared_mailbox` and the operator must write it, because it is the claim
    being made: this is a desk, not a person. An address already attributed to a person, or
    to a different organization, is refused — no command here reassigns an identity somebody
    already recorded.
    """
    return _run(
        command_name=ATTACH_CONTACT_ADDRESS,
        body=body,
        repo=repo,
        operator=operator,
        idempotency_key=idempotency_key,
    )
