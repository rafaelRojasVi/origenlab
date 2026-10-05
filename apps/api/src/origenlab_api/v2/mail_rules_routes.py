"""Email → cases routes (spec 2026-10-05). Admin only.

| Route | Does |
|---|---|
| `GET /v2/workspace/mail-rules/preview` | dry run: every planned action with its rule, reasons, case, institution and quote number; plus the applied actions and whether each was undone |
| `POST /v2/commands/apply-mail-rules` | re-plans from a fresh read and applies the `auto` actions through the existing command handlers; reports what was applied and what was refused, and why |
| `POST /v2/commands/undo-mail-rule-action` | reverses one applied action, as the operator, with a note |

The preview mounts with the V2 read boundary (a dry run needs no write permission and is what the
owner skims before the first apply); apply and undo mount only behind
`ORIGENLAB_V2_COMMANDS_ENABLED`, like every other case command. The client never sends actions:
apply plans again on the server, so what is applied is what the rules say now, not what a page
showed some minutes ago. A `sales` or `viewer` operator is 403 on all three.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from origenlab_api.v2.cockpit_routes import Operator
from origenlab_api.v2.command_routes import Deciding, IdempotencyKey, _detail
from origenlab_api.v2.commands import CommandRefused, require_idempotency_key
from origenlab_api.v2.contact_redaction import ContactRedactingRoute
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.mail_rules_repository import MailRulesRepository

#: Admin only, so nothing is masked in practice; the redacting route class is every V2 GET's,
#: so a later role change cannot leak an address through this one.
mail_rules_preview_router = APIRouter(prefix="/v2/workspace", tags=["v2-mail-rules"],
                                      route_class=ContactRedactingRoute)
mail_rules_command_router = APIRouter(prefix="/v2/commands", tags=["v2-mail-rules"])


def get_mail_rules_repository(request: Request) -> MailRulesRepository:
    repo = getattr(request.app.state, "mail_rules_repository", None)
    if repo is None:  # pragma: no cover - the routers are not mounted without one
        raise HTTPException(status_code=503, detail="the email rules are not configured")
    return repo


Repo = Annotated[MailRulesRepository, Depends(get_mail_rules_repository)]

_ADMIN_ONLY = {"code": "role_may_not_apply_mail_rules",
               "message": "only an admin previews, applies or undoes the email rules"}


def _reading_admin(operator: Operator) -> OperatorIdentity:
    if operator.role != "admin":
        raise HTTPException(status_code=403, detail=_ADMIN_ONLY)
    return operator


def _deciding_admin(operator: Deciding) -> OperatorIdentity:
    if operator.role != "admin":
        raise HTTPException(status_code=403, detail=_ADMIN_ONLY)
    return operator


ReadingAdmin = Annotated[OperatorIdentity, Depends(_reading_admin)]
DecidingAdmin = Annotated[OperatorIdentity, Depends(_deciding_admin)]


class MailRulePair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: Annotated[str, Field(min_length=1, max_length=64)]
    rule_id: Annotated[str, Field(pattern=r"^R[1-8]$")]


class ApplyMailRulesBody(BaseModel):
    """The previewed `(evidence_id, rule_id)` pairs to apply, at most ten per call. The server
    re-plans and applies a pair only if the rules still say the same rule, automatically."""

    model_config = ConfigDict(extra="forbid")

    actions: Annotated[list[MailRulePair], Field(min_length=1, max_length=10)]


class UndoMailRuleActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    receipt_id: Annotated[str, Field(min_length=36, max_length=36)]
    note: Annotated[str, Field(min_length=1, max_length=2000)]


@mail_rules_preview_router.get("/mail-rules/preview")
def preview_mail_rules(_: ReadingAdmin, repo: Repo) -> dict[str, Any]:
    """What the rules would do now, grouped by nothing — the page groups by rule — and the actions
    already applied, newest first. Writes nothing."""
    return repo.preview()


@mail_rules_command_router.post("/apply-mail-rules")
def apply_mail_rules(
    body: ApplyMailRulesBody,
    operator: DecidingAdmin,
    repo: Repo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Apply every `auto` action. Each is one transaction under its own receipt keyed by
    (email, rule), so a second press applies nothing new."""
    try:
        require_idempotency_key(idempotency_key)
        return repo.apply(operator, [p.model_dump() for p in body.actions])
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc


@mail_rules_command_router.post("/undo-mail-rule-action")
def undo_mail_rule_action(
    body: UndoMailRuleActionBody,
    operator: DecidingAdmin,
    repo: Repo,
    idempotency_key: IdempotencyKey = None,
) -> dict[str, Any]:
    """Reverse one applied action. Nothing is deleted; the undo is a person's act on the record."""
    try:
        require_idempotency_key(idempotency_key)
        return repo.undo(operator, body.receipt_id, body.note)
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc
