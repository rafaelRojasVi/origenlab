"""«Pagos» — the SII sales register, the case each document belongs to, and whether it was paid.

| Route | Does |
|---|---|
| `GET /v2/workspace/invoices?opportunity_id=` | every document (or one case's), its balance, and the por-cobrar totals |
| `POST /v2/commands/import-invoices` | multipart: one SII «Registro de Ventas» CSV (`file`); adds the new folios |
| `POST /v2/commands/set-invoice-case` | the document belongs to this case, or to none |
| `POST /v2/commands/mark-invoice-paid` | «Pagada», with the day |
| `POST /v2/commands/mark-invoice-unpaid` | undo «Pagada» |

Any signed-in operator reads; the commands are behind `Deciding` (a `viewer` is 403) and an
`Idempotency-Key`; the three JSON ones also carry the version shown and a non-blank `note`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from origenlab_api.v2.case_document_routes import read_document_form
from origenlab_api.v2.cockpit_routes import Operator
from origenlab_api.v2.command_routes import Deciding, IdempotencyKey, _detail
from origenlab_api.v2.commands import CommandRefused, as_uuid, request_digest, require_idempotency_key
from origenlab_api.v2.contact_redaction import ContactRedactingRoute
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.invoices.commands import (
    IMPORT_INVOICES,
    MARK_INVOICE_PAID,
    MARK_INVOICE_UNPAID,
    SET_INVOICE_CASE,
    MarkInvoicePaidBody,
    MarkInvoiceUnpaidBody,
    SetInvoiceCaseBody,
    validated_invoice,
)
from origenlab_api.v2.invoices.reads import InvoiceReadRepository
from origenlab_api.v2.invoices.repository import V2InvoiceCommandRepository
from origenlab_api.v2.invoices.sii_csv import parse_sales_register

IMPORT_NOTE = "Registro de Ventas del SII subido en «Pagos»."
MAX_CSV_BYTES = 2 * 1024 * 1024

invoice_command_router = APIRouter(prefix="/v2/commands", tags=["v2-commands"])
invoice_read_router = APIRouter(prefix="/v2/workspace", tags=["v2-invoices"], route_class=ContactRedactingRoute)


def get_invoice_command_repository(request: Request) -> V2InvoiceCommandRepository:
    repo = getattr(request.app.state, "v2_invoice_command_repository", None)
    if repo is None:  # pragma: no cover - the router is not mounted without one
        raise HTTPException(status_code=503, detail="the V2 command boundary is not configured")
    return repo


def get_invoice_read_repository(request: Request) -> InvoiceReadRepository:
    repo = getattr(request.app.state, "v2_invoice_read_repository", None)
    if repo is None:  # pragma: no cover - the router is not mounted without one
        raise HTTPException(status_code=503, detail="the V2 read boundary is not configured")
    return repo


CommandRepo = Annotated[V2InvoiceCommandRepository, Depends(get_invoice_command_repository)]
ReadRepo = Annotated[InvoiceReadRepository, Depends(get_invoice_read_repository)]


def _run(*, command_name: str, body: Any, repo: V2InvoiceCommandRepository, reads: InvoiceReadRepository,
         operator: Any, idempotency_key: str | None) -> dict[str, Any]:
    try:
        key = require_idempotency_key(idempotency_key)
        fields = validated_invoice(command_name, body, today=reads.today())
        digest = request_digest(command_name, body)
        return repo.execute(command_name=command_name, operator=operator, fields=fields,
                            idempotency_key=key, digest=digest)
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc


@invoice_read_router.get("/invoices")
def list_invoices(_: Operator, repo: ReadRepo, opportunity_id: Annotated[str | None, Query()] = None) -> dict[str, Any]:
    try:
        case = as_uuid(opportunity_id, "opportunity_id") if opportunity_id is not None else None
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc
    return repo.list(case)


class ImportInvoicesForm(BaseModel):
    """No text field: the form is the file alone."""

    model_config = ConfigDict(extra="forbid")


_IMPORT_OPENAPI = {"requestBody": {"required": True, "content": {"multipart/form-data": {"schema": {
    "type": "object",
    "required": ["file"],
    "properties": {
        "file": {"type": "string", "format": "binary",
                 "description": "the SII «Registro de Ventas» detail CSV (RCV_VENTA_…), at most 2 MiB"},
    },
}}}}}


def _execute_import(request: Request, operator: OperatorIdentity, key: str, data: bytes, filename: str) -> dict[str, Any]:
    try:
        if len(data) > MAX_CSV_BYTES:
            raise CommandRefused(413, "sii_file_too_large", "the file is larger than 2 MiB")
        parsed = parse_sales_register(data)
        sha256 = hashlib.sha256(data).hexdigest()
        fields = {
            "documents": [d.as_fields() for d in parsed.documents],
            "skipped_types": {str(k): v for k, v in sorted(parsed.skipped_types.items())},
            "sha256": sha256,
            "filename": filename,
            "note": IMPORT_NOTE,
        }
        digest = hashlib.sha256(json.dumps({"command": IMPORT_INVOICES, "body": {"sha256": sha256}},
                                           sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        return get_invoice_command_repository(request).execute(
            command_name=IMPORT_INVOICES, operator=operator, fields=fields, idempotency_key=key, digest=digest)
    except CommandRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=_detail(exc)) from exc


@invoice_command_router.post("/import-invoices", openapi_extra=_IMPORT_OPENAPI)
async def import_invoices(request: Request, operator: Deciding, idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    """«Subir CSV del SII»: add the new documents of one month's Registro de Ventas.

    The form is parsed here rather than declared, so an unauthorized caller's upload is never spooled.
    """
    try:
        key = require_idempotency_key(idempotency_key)
    except CommandRefused as exc:
        raise HTTPException(exc.status_code, detail=_detail(exc)) from exc
    _, data, filename = await read_document_form(request, ImportInvoicesForm)
    return await run_in_threadpool(_execute_import, request, operator, key, data, filename)


@invoice_command_router.post("/set-invoice-case")
def set_invoice_case(body: SetInvoiceCaseBody, operator: Deciding, repo: CommandRepo, reads: ReadRepo,
                     idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    """The document belongs to this case (or to none)."""
    return _run(command_name=SET_INVOICE_CASE, body=body, repo=repo, reads=reads, operator=operator,
                idempotency_key=idempotency_key)


@invoice_command_router.post("/mark-invoice-paid")
def mark_invoice_paid(body: MarkInvoicePaidBody, operator: Deciding, repo: CommandRepo, reads: ReadRepo,
                      idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    """«Pagada»: the client paid this factura on `paid_on`."""
    return _run(command_name=MARK_INVOICE_PAID, body=body, repo=repo, reads=reads, operator=operator,
                idempotency_key=idempotency_key)


@invoice_command_router.post("/mark-invoice-unpaid")
def mark_invoice_unpaid(body: MarkInvoiceUnpaidBody, operator: Deciding, repo: CommandRepo, reads: ReadRepo,
                        idempotency_key: IdempotencyKey = None) -> dict[str, Any]:
    """Undo «Pagada»."""
    return _run(command_name=MARK_INVOICE_UNPAID, body=body, repo=repo, reads=reads, operator=operator,
                idempotency_key=idempotency_key)
