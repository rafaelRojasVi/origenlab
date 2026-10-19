"""Ask for a bid preparation, and read its files (spec 2026-10-19 §6.4, plan Task 7).

`POST /v2/commands/prepare-tender-bid {opportunity_id, phase}` queues one `evidence.bid_preparation`
row and writes `opportunity.bid_preparation_requested`; the worker (a later task) runs it.

* `read` takes every live `tender_annex` of the case.
* `fill` takes the case's newest `ready` read (and that read's annexes) plus the offer: the current
  revision of the newest quote on the case. The offer must be APPROVED in the Cotizador (ruling
  R9: no price leaves unseen), and the bidder's profile must exist.

Refusals, in the order they are checked:

| Status | Code | When |
|---|---|---|
| 404 | `case_not_found` | no such case |
| 422 | `not_a_tender_case` | the case is a sale |
| 422 | `no_annexes` | the case has no live anexo |
| 422 | `read_not_ready` | `fill` with no `ready` read |
| 422 | `offer_quote_missing` | `fill` with no quote revision on the case |
| 422 | `offer_not_approved` | `fill` and the offer's revision is neither `approved` nor `sent` (ruling R9) |
| 422 | `bidder_profile_missing` | `fill` with no bidder profile saved |
| 409 | `preparation_running` | this phase is already queued or running on the case |
| 409 | `model_budget_reached` | this month's spend is at the cap; `details` is the spend summary |

Output files are read with `GET /v2/tenders/{id}/preparations/{prep_id}/files/{file_key}/url`; the
worker writes them at `opportunities/<case>/bid/<prep_id>/<file_key>.<ext>` and records each in
`outputs` as `{file_key, filename, kind, completar_count, storage_path}`. The page never shows the path.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal

import psycopg.errors
from pydantic import BaseModel, ConfigDict

from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.model_spend import FEATURE_LICITACIONES, spend_summary
from origenlab_api.v2.tenders.bidder import current_bidder_profile

PREPARE_TENDER_BID = "prepare-tender-bid"
FILE_KEY = re.compile(r"^[a-z0-9_-]{1,64}$")
_EXTENSION = re.compile(r"[.](pdf|docx|xlsx|doc|xls|zip|jpg|png)$")


class PrepareTenderBidBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    opportunity_id: str
    phase: Literal["read", "fill"]


def _running_refusal() -> CommandRefused:
    return CommandRefused(409, "preparation_running",
                          "Ya hay una preparación en curso para esta licitación; espera a que termine.")


def _offer_revision(cur: Any, opportunity_id: str) -> tuple[str, str] | None:
    """(revision id, status) of the current revision of the newest quote on the case, or None."""
    cur.execute(
        """select r.id::text, r.status
             from crm.quote q
             join crm.quote_revision r on r.quote_id = q.id
            where q.opportunity_id = %s::uuid and r.origin = 'authored'
              and r.superseded_by_revision_no is null and r.status <> 'void'
            order by r.created_at desc, r.revision_no desc, r.id desc limit 1""", (opportunity_id,))
    row = cur.fetchone()
    return (row[0], row[1]) if row else None


def request_bid_preparation(
    repo: Any, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
) -> dict[str, Any]:
    opportunity_id, phase = fields["opportunity_id"], fields["phase"]
    cur.execute("select kind from crm.opportunity where id = %s::uuid for update", (opportunity_id,))
    case = cur.fetchone()
    if case is None:
        raise CommandRefused(404, "case_not_found", "no such case")
    if case[0] != "tender":
        raise CommandRefused(422, "not_a_tender_case", "La preparación es de una licitación.")
    cur.execute(
        "select id from crm.opportunity_document where opportunity_id = %s::uuid and kind = 'tender_annex' "
        "and removed_at is null order by attached_at, id", (opportunity_id,))
    annex_ids = [r[0] for r in cur.fetchall()]
    if not annex_ids:
        raise CommandRefused(422, "no_annexes", "Sube los anexos de la licitación antes de preparar la oferta.")
    based_on = revision_id = None
    if phase == "fill":
        cur.execute(
            "select id::text, annex_document_ids from evidence.bid_preparation where opportunity_id = %s::uuid "
            "and phase = 'read' and status = 'ready' order by requested_at desc, id desc limit 1",
            (opportunity_id,))
        read = cur.fetchone()
        if read is None:
            raise CommandRefused(422, "read_not_ready", "Primero hay que leer los anexos.")
        based_on = read[0]
        live = set(annex_ids)
        annex_ids = [a for a in read[1] if a in live]
        if not annex_ids:
            raise CommandRefused(422, "no_annexes", "Los anexos leídos ya no están; sube los anexos y vuelve a leerlos.")
        offer = _offer_revision(cur, opportunity_id)
        if offer is None:
            raise CommandRefused(422, "offer_quote_missing", "Arma la cotización de la oferta en el Cotizador.")
        revision_id = offer[0]
        if offer[1] not in ("approved", "sent"):
            raise CommandRefused(
                422, "offer_not_approved", "Aprueba la cotización de la oferta en el Cotizador antes de preparar.")
        if current_bidder_profile(cur) is None:
            raise CommandRefused(422, "bidder_profile_missing", "Completa los datos del oferente primero.")
    cur.execute(
        "select 1 from evidence.bid_preparation where opportunity_id = %s::uuid and phase = %s "
        "and status in ('queued', 'running')", (opportunity_id, phase))
    if cur.fetchone() is not None:
        raise _running_refusal()
    summary = spend_summary(cur, FEATURE_LICITACIONES, datetime.now(UTC))
    if summary["reached"]:
        raise CommandRefused(409, "model_budget_reached",
                             "Se alcanzó el tope mensual de gasto en modelos; vuelve a intentar el mes próximo.",
                             details=summary)
    try:
        cur.execute("savepoint prepare_tender_bid")
        cur.execute(
            """insert into evidence.bid_preparation
                   (opportunity_id, phase, annex_document_ids, based_on_preparation_id, quote_revision_id,
                    requested_by_operator_id)
               values (%s::uuid, %s, %s::uuid[], %s::uuid, %s::uuid, %s::uuid) returning id::text""",
            (opportunity_id, phase, annex_ids, based_on, revision_id, operator.operator_id))
    except psycopg.errors.UniqueViolation:
        cur.execute("rollback to savepoint prepare_tender_bid")
        raise _running_refusal() from None
    preparation_id = cur.fetchone()[0]
    event_id = repo._append_event(
        cur, aggregate_kind="opportunity", aggregate_id=opportunity_id,
        event_type="opportunity.bid_preparation_requested",
        payload={"preparation_id": preparation_id, "phase": phase,
                 "annex_document_ids": [str(a) for a in annex_ids],
                 "based_on_preparation_id": based_on, "quote_revision_id": revision_id},
        operator=operator, receipt_id=receipt_id)
    return {"command": PREPARE_TENDER_BID, "opportunity_id": opportunity_id, "preparation_id": preparation_id,
            "phase": phase, "event_ids": [event_id]}


def public_outputs(outputs: Any) -> list[dict[str, Any]]:
    """The outputs as the page shows them: `{file_key, filename, kind, completar_count}`, never a path."""
    shown = []
    for o in outputs if isinstance(outputs, list) else []:
        if isinstance(o, dict):
            shown.append({k: o.get(k) for k in ("file_key", "filename", "kind", "completar_count")})
    return shown


def output_storage_path(case_id: str, preparation_id: str, output: dict[str, Any]) -> str | None:
    """The object path of one output: the worker's recorded `storage_path`, else the convention
    `opportunities/<case>/bid/<prep_id>/<file_key>.<ext>` with the extension of its filename."""
    recorded = output.get("storage_path")
    prefix = f"opportunities/{case_id}/bid/{preparation_id}/{output['file_key']}."
    if isinstance(recorded, str) and recorded.startswith(prefix) and ".." not in recorded:
        return recorded
    ext = _EXTENSION.search(str(output.get("filename") or "").lower())
    if ext is None:
        return None
    return f"opportunities/{case_id}/bid/{preparation_id}/{output['file_key']}{ext.group(0)}"
