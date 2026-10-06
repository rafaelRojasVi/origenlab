"""The commercial-case command boundary — `POST /v2/commands/*`.

| Route | Records |
|---|---|
| `POST /v2/commands/open-commercial-case` | a case, and the document that is the reason it exists |
| `POST /v2/commands/link-case-evidence` | why this case believes something — including against it |
| `POST /v2/commands/add-case-organization` | an institution is on this case, in this part |
| `POST /v2/commands/set-case-organization-role` | an institution's part is confirmed, or it is this other part now |
| `POST /v2/commands/record-case-interest` | what the case is seeking |
| `POST /v2/commands/advance-case-stage` | the case moved, and the rules for moving it were met |
| `POST /v2/commands/record-case-won` | the case was won, against this sent, current quote revision |
| `POST /v2/commands/resolve-current-revision` | of this quote's current revisions, this one is the current one |
| `POST /v2/commands/record-case-quotation` | this quote was already sent on this case, by this Gmail message |

**Why a third router.** `routes.py` is the read boundary and a test over its own methods
asserts it has no POST; `command_routes.py` is the evidence-review write path. This is the
case write path, and it is separate for the same reason those two are: "what can write to
`crm.opportunity*`?" should have a one-file answer, and adding nine routes to a module whose
docstring says *four evidence-review commands* would have made that answer a paragraph.

All three mount together behind the same switch. `ORIGENLAB_V2_COMMANDS_ENABLED` is what
turns a configured read surface into one that records durable decisions, and nothing here
changes that: off, every path below is a 404.

**Four requirements, on every route, before anything runs.** An active operator whose role is
`sales` or `admin`; an `Idempotency-Key` header; the case the decision is about, with the
version of it the operator was shown; and a non-blank `note` saying why. Three of the four
are in the request and the fourth is not — the operator comes from the verified identity,
never from the body, so a caller cannot decide *as* somebody else.

**The dashboard reaches four of these routes, and only four.** `apps/dashboard-proxy` lists
`advance-case-stage`, `record-case-won`, `resolve-current-revision` and `record-case-quotation`
by name (`CASE_COMMAND_POST_PATHS`), behind the same Origin / JSON / `Idempotency-Key` guard as
the other browser commands: they back the case drawer's «Cambiar etapa», «Marcar ganada»,
«Elegir revisión vigente» and «Registrar cotización» / «Nueva revisión». The other five — opening a case, linking
evidence, naming an institution or its part, recording an interest — stay refused at the
Worker; a test in its suite names every case command path and asserts exactly that split, so
widening it is a reviewed change rather than an accident.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from origenlab_api.v2.case_command_repository import V2CaseCommandRepository
from origenlab_api.v2.case_commands import (
    ADD_CASE_ORGANIZATION,
    ADVANCE_CASE_STAGE,
    LINK_CASE_EVIDENCE,
    OPEN_COMMERCIAL_CASE,
    RECORD_CASE_INTEREST,
    RECORD_CASE_QUOTATION,
    RECORD_CASE_WON,
    RESOLVE_CURRENT_REVISION,
    SET_CASE_ORGANIZATION_ROLE,
    AddCaseOrganizationBody,
    AdvanceCaseStageBody,
    LinkCaseEvidenceBody,
    OpenCommercialCaseBody,
    RecordCaseInterestBody,
    RecordCaseQuotationBody,
    RecordCaseWonBody,
    ResolveCurrentRevisionBody,
    SetCaseOrganizationRoleBody,
    validated_case,
)
from origenlab_api.v2.command_routes import Deciding, IdempotencyKey, _detail
from origenlab_api.v2.commands import CommandRefused, request_digest, require_idempotency_key
from origenlab_api.v2.identity import OperatorIdentity

case_command_router = APIRouter(prefix="/v2/commands", tags=["v2-commands"])


def get_case_command_repository(request: Request) -> V2CaseCommandRepository:
    repo = getattr(request.app.state, "v2_case_command_repository", None)
    if repo is None:  # pragma: no cover - the router is not mounted without one
        raise HTTPException(status_code=503, detail="the V2 command boundary is not configured")
    return repo



def _run(
    *,
    command_name: str,
    body: Any,
    repo: V2CaseCommandRepository,
    operator: OperatorIdentity,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """The same five steps for every command, so no route can quietly skip one."""
    try:
        key = require_idempotency_key(idempotency_key)
        fields = validated_case(command_name, body)
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


@case_command_router.post("/open-commercial-case")
def open_commercial_case(
    body: OpenCommercialCaseBody,
    operator: Deciding,
    repo: V2CaseCommandRepository = Depends(get_case_command_repository),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Open a case at `lead`, with the document that is the reason it exists.

    The case and its `origin` evidence link are one transaction: a case with nothing behind
    it is not refused by a check, it is unrequestable, because the origin record is a required
    field of the only request that can create one.

    It names no institution. An enquiry from a name nobody has recorded is a real case on the
    day it arrives, and forcing an institution onto it would mean guessing one — so the case
    opens with `organization_id` null, which is the truth, and `add-case-organization` is
    where an operator says who is asking.
    """
    return _run(
        command_name=OPEN_COMMERCIAL_CASE,
        body=body,
        repo=repo,
        operator=operator,
        idempotency_key=idempotency_key,
    )


@case_command_router.post("/link-case-evidence")
def link_case_evidence(
    body: LinkCaseEvidenceBody,
    operator: Deciding,
    repo: V2CaseCommandRepository = Depends(get_case_command_repository),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Attach a document that already exists, as the reason the case believes something.

    Exactly one typed subject — an evidence record, an assertion, a message or a procurement
    notice — and it must already exist: this route creates no evidence, which is why a wrong
    id is a 404 rather than a new row.

    `contradicts` is a first-class relation. A case that has collected the evidence against
    itself is a case that can be closed honestly, and this is the only durable place that
    reading can live.
    """
    return _run(
        command_name=LINK_CASE_EVIDENCE,
        body=body,
        repo=repo,
        operator=operator,
        idempotency_key=idempotency_key,
    )


@case_command_router.post("/add-case-organization")
def add_case_organization(
    body: AddCaseOrganizationBody,
    operator: Deciding,
    repo: V2CaseCommandRepository = Depends(get_case_command_repository),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Put an institution on the case, in a part the operator names out loud.

    `role` has no default, and `mentioned` — named in the evidence, part not decided — is a
    value rather than a shrug. It is also the only part a machine may ever propose, which a
    CHECK enforces; nothing that reaches this route is a machine.

    `requesting_institution` does two things in one transaction: it opens the role row and it
    sets `crm.opportunity.organization_id`, which is *defined* as that institution. An
    institution OrigenLab buys from is refused in that part unless the request carries
    `supplier_exception_reason` — a sentence, written once with the operator and the moment,
    never rewritten, scoped to this case, granting nothing anywhere else.
    """
    return _run(
        command_name=ADD_CASE_ORGANIZATION,
        body=body,
        repo=repo,
        operator=operator,
        idempotency_key=idempotency_key,
    )


@case_command_router.post("/set-case-organization-role")
def set_case_organization_role(
    body: SetCaseOrganizationRoleBody,
    operator: Deciding,
    repo: V2CaseCommandRepository = Depends(get_case_command_repository),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Vouch for what an institution is to this case, or replace that reading with another.

    Nothing is edited. `role` is not in the runtime role's UPDATE grant, so changing a part
    closes the current row and opens a new one in the same transaction; both stay readable,
    which is what "a wrong decision is corrected, not erased" means in rows.

    The single in-place move is confirming a proposal the operator agrees with: a
    `machine_proposed` row becomes `confirmed`, because the reading did not change — only who
    vouches for it. Confirming a part that is already confirmed is refused rather than
    reported as success.
    """
    return _run(
        command_name=SET_CASE_ORGANIZATION_ROLE,
        body=body,
        repo=repo,
        operator=operator,
        idempotency_key=idempotency_key,
    )


@case_command_router.post("/record-case-interest")
def record_case_interest(
    body: RecordCaseInterestBody,
    operator: Deciding,
    repo: V2CaseCommandRepository = Depends(get_case_command_repository),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Record what the case is seeking: a product, a manufacturer, a model string, or several.

    A quantity at most, and never an amount, a currency or a margin — an interest is not a
    commitment and not a price. The request body forbids unknown fields, so one carrying a
    price is refused rather than silently stripped.

    Naming a manufacturer creates nothing for that manufacturer: no relationship, no prospect,
    no marketing permission and no part on the case. If they belong on the case, that is
    `add-case-organization`, said out loud.
    """
    return _run(
        command_name=RECORD_CASE_INTEREST,
        body=body,
        repo=repo,
        operator=operator,
        idempotency_key=idempotency_key,
    )


@case_command_router.post("/advance-case-stage")
def advance_case_stage(
    body: AdvanceCaseStageBody,
    operator: Deciding,
    repo: V2CaseCommandRepository = Depends(get_case_command_repository),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Move the case along `WORKFLOWS.md` §1.1, and refuse by name when a rule is not met.

    From `qualified` onward a case must record who is asking, confirmed by a human. That is
    not this route's rule and cannot be worked around by not using it: three shipped database
    guards compose into it, and this check exists only to turn a constraint violation into a
    sentence an operator can act on.

    `lost` and `abandoned` need a motive and nothing else may carry one. `won` is refused
    outright — a case is won against a specific quote revision, which this request cannot name;
    that is `record-case-won` — because saying so is more useful than a constraint violation.

    A terminal case is never revived here or anywhere: the boundary refuses it, and so does a
    trigger on `crm.opportunity`.
    """
    return _run(
        command_name=ADVANCE_CASE_STAGE,
        body=body,
        repo=repo,
        operator=operator,
        idempotency_key=idempotency_key,
    )


@case_command_router.post("/record-case-won")
def record_case_won(
    body: RecordCaseWonBody,
    operator: Deciding,
    repo: V2CaseCommandRepository = Depends(get_case_command_repository),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Record that the case was won, against one sent, current quote revision on it.

    The case must be at `negotiating` — the one stage `won` follows — and the request names the
    revision as `quote_id` + `revision_no`. A revision on another case is a 404; one that is not
    `sent`, or that a later revision replaced, is a 409. The writer is the same one the email →
    cases rules use for a purchase order (`case_won.py`), but here every event is the operator's:
    the identity comes from the verified session and is never marked as the system.
    """
    return _run(
        command_name=RECORD_CASE_WON,
        body=body,
        repo=repo,
        operator=operator,
        idempotency_key=idempotency_key,
    )


@case_command_router.post("/resolve-current-revision")
def resolve_current_revision(
    body: ResolveCurrentRevisionBody,
    operator: Deciding,
    repo: V2CaseCommandRepository = Depends(get_case_command_repository),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Settle «Hay más de una revisión vigente»: name the quote revision that is the current one.

    Every other current revision of that quote is superseded by the chosen one in one
    transaction — `superseded_by_revision_no` + `superseded_at` and a `quote_revision.superseded`
    event each — and the case version advances. Nothing is voided or deleted. The chosen revision
    must be `sent` and not superseded (409 `quote_revision_not_current`); a quote with one current
    revision has nothing to resolve (409 `nothing_to_resolve`); a quote on another case is a 404.
    """
    return _run(
        command_name=RESOLVE_CURRENT_REVISION,
        body=body,
        repo=repo,
        operator=operator,
        idempotency_key=idempotency_key,
    )


@case_command_router.post("/record-case-quotation")
def record_case_quotation(
    body: RecordCaseQuotationBody,
    operator: Deciding,
    repo: V2CaseCommandRepository = Depends(get_case_command_repository),
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Record a quote already sent on this case: its printed number, its document, the message.

    The message is a Gmail evidence record already linked to the case (404 `message_not_on_case`
    otherwise), and it must carry the document. The quote and its `sent` revision are written by
    the same writer the historical import and the email → cases rules R3/R4 use
    (`case_quotation.py`), so the same refusals hold — among them a printed number that is
    already a quote on another case (409 `number_on_other_opportunity`). With
    `supersedes_revision_no` it is «Nueva revisión» of the case's quote with that number.
    """
    return _run(
        command_name=RECORD_CASE_QUOTATION,
        body=body,
        repo=repo,
        operator=operator,
        idempotency_key=idempotency_key,
    )
