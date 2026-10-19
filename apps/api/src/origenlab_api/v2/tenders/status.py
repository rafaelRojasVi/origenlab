"""`set-tender-status`: the estado of a tender (spec 2026-10-19 §6.10).

`preparando`, `postulada` and `perdida` are the operator's to record, in any order. `adjudicada` is
refused until phase 3 (award capture): 422 `award_is_phase_3`. `perdida` needs a `reason`
(422 `reason_required`). Both are checked before the transaction, so a refused request claims no
receipt. The stage machine is left as it is: a tender's estado is its own column, and a case never
closes by it.

Refusals:

| Status | Code | When |
|---|---|---|
| 422 | `award_is_phase_3` | `status = adjudicada` |
| 422 | `reason_required` | `status = perdida` with no reason |
| 404 | `tender_not_found` | no tender case has this id (a sale case is not one) |
| 409 | `case_version_conflict` | `expected_version` is not the version the operator was shown |
| 409 | `tender_already_at_status` | the tender is already at that estado |
| 409 | `tender_status_is_final` | the tender is `adjudicada` (cannot happen before phase 3) |

Event: `opportunity.tender_status_set`, payload `{from_status, to_status, reason}`.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import OperatorIdentity

SET_TENDER_STATUS = "set-tender-status"


class SetTenderStatusBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    opportunity_id: str
    status: Literal["preparando", "postulada", "adjudicada", "perdida"]
    expected_version: Annotated[int, Field(ge=1)]
    reason: Annotated[str, Field(max_length=2000)] | None = None


def prepare_set_tender_status(body: SetTenderStatusBody) -> dict[str, Any]:
    """The checks that need no database, then the handler's fields."""
    if body.status == "adjudicada":
        raise CommandRefused(
            422, "award_is_phase_3",
            "Registrar una licitación como adjudicada llega con la fase 3; por ahora usa «Postulada» o «Perdida».")
    reason = body.reason or None
    if body.status == "perdida" and reason is None:
        raise CommandRefused(422, "reason_required", "Para marcar la licitación como perdida, escribe el motivo.")
    return {"opportunity_id": body.opportunity_id, "status": body.status,
            "expected_version": body.expected_version, "reason": reason}


def set_tender_status(
    repo: Any, cur: Any, operator: OperatorIdentity, fields: dict[str, Any], receipt_id: str
) -> dict[str, Any]:
    cur.execute(
        "select id::text, version, tender_status from crm.opportunity "
        "where id = %s::uuid and kind = 'tender' for update",
        (fields["opportunity_id"],),
    )
    case = cur.fetchone()
    if case is None:
        raise CommandRefused(404, "tender_not_found", "No existe esa licitación.")
    case_id, version, current = case[0], int(case[1]), case[2]
    if version != int(fields["expected_version"]):
        raise CommandRefused(
            409, "case_version_conflict",
            f"La licitación cambió desde que la viste (versión {fields['expected_version']}, ahora {version}); "
            "vuelve a leerla.")
    if current == "adjudicada":
        raise CommandRefused(409, "tender_status_is_final", "Una licitación adjudicada no cambia de estado.")
    target = fields["status"]
    if current == target:
        raise CommandRefused(409, "tender_already_at_status", f"La licitación ya está «{target}».")
    cur.execute(
        "update crm.opportunity set tender_status = %s, version = version + 1, updated_at = now() "
        "where id = %s::uuid and version = %s",
        (target, case_id, version),
    )
    if cur.rowcount != 1:
        raise CommandRefused(409, "case_version_conflict", "La licitación cambió mientras se guardaba; vuelve a leerla.")
    event_id = repo._append_event(
        cur, aggregate_kind="opportunity", aggregate_id=case_id, event_type="opportunity.tender_status_set",
        payload={"from_status": current, "to_status": target, "reason": fields["reason"]},
        operator=operator, receipt_id=receipt_id,
    )
    return {
        "command": SET_TENDER_STATUS,
        "opportunity_id": case_id,
        "opportunity_version": version + 1,
        "tender_status": target,
        "previous_status": current,
        "created": [],
        "event_ids": [event_id],
    }
