"""Quote routes, mounted only behind ORIGENLAB_V2_QUOTING_ENABLED (inside `main._mount_catalog`).

Reads (`/v2/quotes/*`): the Cotizador is for sales and admin only — every answer carries costs and
margins — so a viewer gets 403 `role_may_not_view_costs` before anything is read (spec 2026-10-15
§6: viewers do not get the Cotizador). `POST /v2/quotes/preview` is a read (R-A4): no
Idempotency-Key, no receipt, no event, nothing written; its body is the same `QuoteDraft` the save
takes, checked by Pydantic with `extra="forbid"`, and Labdelivery text in it is refused (422).

Commands (`/v2/commands/<quote-command>`): an active `sales` or `admin` operator (viewer → 403
`role_may_not_decide`), an `Idempotency-Key`, a body with `extra="forbid"`; the handlers live in
`commands.V2QuoteRepository`, reached through the catalog's `_run` path.
"""
from __future__ import annotations

from typing import Any, NoReturn
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from origenlab_api.v2.catalog.commands import _submitted_text
from origenlab_api.v2.catalog.keys import LabdeliveryRefused, refuse_labdelivery
from origenlab_api.v2.catalog.routes import _attach_margins
from origenlab_api.v2.cockpit_routes import Operator
from origenlab_api.v2.command_routes import Deciding, IdempotencyKey, _detail
from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.crm_authoring_routes import _run
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.quotes.draft import (
    ApproveQuoteBody,
    CreateQuoteBody,
    NewQuoteRevisionBody,
    PreviewQuoteBody,
    SaveQuoteDraftBody,
    VoidQuoteBody,
)
from origenlab_api.v2.quotes.reads import V2QuoteReads

quote_read_router = APIRouter(prefix="/v2/quotes")
quote_command_router = APIRouter(prefix="/v2/commands", tags=["v2-quote-commands"])


def _reads(request: Request) -> V2QuoteReads:
    return request.app.state.quote_reads


def _may_view_costs(operator: OperatorIdentity) -> None:
    if operator.role not in ("sales", "admin"):
        raise HTTPException(403, detail={"code": "role_may_not_view_costs",
                                         "message": "this role may not view quotes"})


def _refused(exc: CommandRefused) -> NoReturn:
    raise HTTPException(exc.status_code, detail=_detail(exc)) from exc


# Declared before `/{quote_id}`: «numbering» is not a quote id.
@quote_read_router.get("/numbering")
def numbering(request: Request, operator: Operator) -> dict:
    _may_view_costs(operator)
    try:
        return _reads(request).numbering()
    except CommandRefused as exc:  # a bad seed: the operator's 409, not a 500
        _refused(exc)


@quote_read_router.post("/preview")
def preview(body: PreviewQuoteBody, request: Request, operator: Operator) -> dict:
    _may_view_costs(operator)
    try:
        refuse_labdelivery(*_submitted_text(body.model_dump(mode="json")))
    except LabdeliveryRefused:
        raise HTTPException(422, detail={"code": "labdelivery_refused",
                                         "message": "input of Labdelivery origin is refused"}) from None
    try:
        return _reads(request).preview(body)
    except CommandRefused as exc:
        _refused(exc)


@quote_read_router.get("/{quote_id}")
def workspace(quote_id: UUID, request: Request, operator: Operator) -> dict:
    _may_view_costs(operator)
    found = _reads(request).workspace(quote_id)
    if found is None:
        raise HTTPException(404, detail={"code": "quote_not_found", "message": "quote not found"})
    if isinstance(found.get("price_history"), dict):
        _attach_margins(request, found["price_history"], operator.role)
    return found


# ──────────────────────────────────────────────────────────── command routes ──

def _command(command_name: str, body: BaseModel, request: Request, operator: Any,
             idempotency_key: str | None) -> dict[str, Any]:
    return _run(command_name=command_name, body=body, fields=body.model_dump(mode="json"),
                repo=request.app.state.quote_repository, operator=operator, idempotency_key=idempotency_key)


@quote_command_router.post("/create-quote")
def create_quote(body: CreateQuoteBody, request: Request, operator: Deciding,
                 idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    return _command("create-quote", body, request, operator, idempotency_key)


@quote_command_router.post("/save-quote-draft")
def save_quote_draft(body: SaveQuoteDraftBody, request: Request, operator: Deciding,
                     idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    return _command("save-quote-draft", body, request, operator, idempotency_key)


@quote_command_router.post("/approve-quote-revision")
def approve_quote_revision(body: ApproveQuoteBody, request: Request, operator: Deciding,
                           idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    return _command("approve-quote-revision", body, request, operator, idempotency_key)


@quote_command_router.post("/void-quote-revision")
def void_quote_revision(body: VoidQuoteBody, request: Request, operator: Deciding,
                        idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    return _command("void-quote-revision", body, request, operator, idempotency_key)


@quote_command_router.post("/create-quote-revision")
def create_quote_revision(body: NewQuoteRevisionBody, request: Request, operator: Deciding,
                          idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    return _command("create-quote-revision", body, request, operator, idempotency_key)
