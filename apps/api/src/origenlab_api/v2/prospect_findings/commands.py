"""«Prospectos» commands: a person's verdict on a prospect finding, and its promotion into the CRM.

`review-prospect` records `mas_tarde` (with a future date), `descartar` (with a reason) or `reabrir`
in `crm.prospect_review` — append-only, the latest row is the current verdict.

`contact-prospect` is the promotion of DOMAIN.md §5, in one transaction. It re-reads the finding's
classification under a lock (Bajas may have changed since the list was read), then:

* institution — the one chosen (`organization_id`), else exactly one candidate by domain or name is
  linked, none is registered (`create_new_organization` forces that); two or more without a choice
  refuse with the candidates. A linked institution gets the `prospect` role unless it already holds
  a current `customer` or `prospect` role;
* person — the owner of the email's contact point if it has one, else a new person who claims the
  email (an ownerless contact point is claimed, not duplicated); affiliated to the institution;
* evidence — every assertion resolved, the source record `promoted`, a `contactar` verdict whose
  reason names where the contact data came from (the legal trail, Ley 21.719).

It reuses the CRM authoring handlers in-process with the same cursor and receipt.

Refusals:
| Status | Code | When |
|---|---|---|
| 404 | `prospect_not_found` | no finding has this id |
| 409 | `prospect_blocked` | the address or its domain is in Bajas |
| 409 | `prospect_already_contacted` | the finding's latest verdict is already `contactar` |
| 409 | `organization_ambiguous` | two or more institutions match and none was chosen; `candidates` |
| 404 | `organization_not_found` | the chosen institution does not exist |
| 422 | `reason_required` | `descartar` without a reason |
| 422 | `remind_on_must_be_future` | `mas_tarde` without a date after today (America/Santiago) |
| 409 | `prospect_already_open` | `reabrir` on a finding that is not discarded or postponed |

Event: `source_record.prospect_reviewed` (aggregate `source_record`), payload
`{verdict, previous_verdict, reason, remind_on}`.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from origenlab_api.v2 import crm_authoring as authoring
from origenlab_api.v2.commands import CommandRefused, as_uuid
from origenlab_api.v2.crm_authoring import V2CrmAuthoringRepository
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.prospect_findings.classify import FREE_MAIL_DOMAINS, classify, domain_of

REVIEW_PROSPECT = "review-prospect"
CONTACT_PROSPECT = "contact-prospect"
SANTIAGO = ZoneInfo("America/Santiago")
_NOTE = "Agregado desde «Prospectos»"


class ReviewProspectBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    finding_id: str
    verdict: Literal["mas_tarde", "descartar", "reabrir"]
    reason: Annotated[str, Field(max_length=500)] | None = None
    remind_on: date | None = None


class ContactProspectBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    finding_id: str
    organization_id: str | None = None
    create_new_organization: bool = False


def prepare_review_prospect(body: ReviewProspectBody, today: date | None = None) -> dict[str, Any]:
    today = today or datetime.now(SANTIAGO).date()
    if body.verdict == "descartar" and not body.reason:
        raise CommandRefused(422, "reason_required", "Di por qué lo descartas.")
    if body.verdict == "mas_tarde" and (body.remind_on is None or body.remind_on <= today):
        raise CommandRefused(422, "remind_on_must_be_future", "Elige una fecha a partir de mañana.")
    return {"finding_id": as_uuid(body.finding_id, "finding_id"), "verdict": body.verdict,
            "reason": body.reason or None,
            "remind_on": body.remind_on.isoformat() if body.verdict == "mas_tarde" and body.remind_on else None}


def prepare_contact_prospect(body: ContactProspectBody) -> dict[str, Any]:
    return {"finding_id": as_uuid(body.finding_id, "finding_id"),
            "organization_id": as_uuid(body.organization_id, "organization_id") if body.organization_id else None,
            "create_new_organization": bool(body.create_new_organization)}


def _lock_finding(cur: Any, finding_id: str) -> dict[str, Any]:
    cur.execute("select id::text, payload from evidence.source_record "
                "where id = %s::uuid and kind = 'prospect_finding' for update", (finding_id,))
    row = cur.fetchone()
    if row is None:
        raise CommandRefused(404, "prospect_not_found", "Ese prospecto no existe.")
    return {"id": row[0], "payload": row[1] or {}}


def _latest_verdict(cur: Any, finding_id: str) -> str | None:
    cur.execute("select verdict from crm.prospect_review where source_record_id = %s::uuid "
                "order by reviewed_at desc, id desc limit 1", (finding_id,))
    row = cur.fetchone()
    return row[0] if row else None


def _record_verdict(repo: Any, cur: Any, operator: OperatorIdentity, receipt_id: str, *, finding_id: str,
                    verdict: str, previous: str | None, reason: str | None, remind_on: str | None) -> tuple[str, str]:
    cur.execute("insert into crm.prospect_review (source_record_id, verdict, reason, remind_on, reviewed_by_operator_id) "
                "values (%s::uuid, %s, %s, %s::date, %s::uuid) returning id::text",
                (finding_id, verdict, reason, remind_on, operator.operator_id))
    (review_id,) = cur.fetchone()
    event_id = repo._append_event(
        cur, aggregate_kind="source_record", aggregate_id=finding_id, event_type="source_record.prospect_reviewed",
        payload={"verdict": verdict, "previous_verdict": previous, "reason": reason, "remind_on": remind_on},
        operator=operator, receipt_id=receipt_id)
    return review_id, event_id


def review_prospect(repo: Any, cur: Any, operator: OperatorIdentity, fields: dict[str, Any],
                    receipt_id: str) -> dict[str, Any]:
    finding = _lock_finding(cur, fields["finding_id"])
    previous = _latest_verdict(cur, finding["id"])
    if fields["verdict"] == "reabrir" and previous not in {"descartar", "mas_tarde"}:
        raise CommandRefused(409, "prospect_already_open", "Este prospecto ya está por revisar.")
    if previous == "contactar":
        raise CommandRefused(409, "prospect_already_contacted", "Este prospecto ya está en el CRM.")
    review_id, event_id = _record_verdict(repo, cur, operator, receipt_id, finding_id=finding["id"],
                                          verdict=fields["verdict"], previous=previous, reason=fields["reason"],
                                          remind_on=fields["remind_on"])
    return {"command": REVIEW_PROSPECT, "finding_id": finding["id"], "review_id": review_id,
            "verdict": fields["verdict"], "previous_verdict": previous, "created": ["prospect_review"],
            "event_ids": [event_id]}


def _kind_for(payload: dict[str, Any]) -> str:
    if str(payload.get("source", "")).startswith("industria_"):
        return "laboratory"
    return "university" if "universidad" in str(payload.get("organization_name", "")).lower() else "other"


def _organization(repo: Any, cur: Any, operator: OperatorIdentity, receipt_id: str, fields: dict[str, Any],
                  payload: dict[str, Any], candidates: list[dict[str, str]]) -> tuple[str, bool]:
    """The institution's id and whether it was created here."""
    if fields["organization_id"]:
        cur.execute("select id::text from crm.organization where id = %s::uuid and merged_into_organization_id is null",
                    (fields["organization_id"],))
        row = cur.fetchone()
        if row is None:
            raise CommandRefused(404, "organization_not_found", "Esa institución no existe.")
        return row[0], False
    if len(candidates) == 1 and not fields["create_new_organization"]:
        return candidates[0]["id"], False
    if len(candidates) > 1 and not fields["create_new_organization"]:
        raise CommandRefused(409, "organization_ambiguous", "¿Cuál de estas instituciones es?",
                             details={"candidates": candidates})
    domain = domain_of(payload.get("email"), payload.get("website"))
    taken = False
    if domain and domain not in FREE_MAIL_DOMAINS:
        cur.execute("select 1 from crm.organization_domain where domain_norm = %s limit 1", (domain,))
        taken = cur.fetchone() is not None
    created = authoring._handle_register_organization(repo, cur, operator, {
        "name": payload["organization_name"], "legal_name": None, "kind": _kind_for(payload),
        "classification": "prospect", "product_lines": [], "note": _NOTE,
        "domain": domain if domain and domain not in FREE_MAIL_DOMAINS and not taken else None,
    }, receipt_id)
    return created["organization_id"], True


def _ensure_prospect_role(repo: Any, cur: Any, operator: OperatorIdentity, receipt_id: str, org_id: str) -> None:
    cur.execute("select 1 from crm.organization_relationship where organization_id = %s::uuid "
                "and role in ('customer', 'prospect') and (valid_to is null or valid_to >= current_date) limit 1",
                (org_id,))
    if cur.fetchone() is not None:
        return
    cur.execute("select version from crm.organization where id = %s::uuid", (org_id,))
    (version,) = cur.fetchone()
    authoring._handle_add_organization_classification(repo, cur, operator, {
        "organization_id": org_id, "expected_version": version, "role": "prospect", "note": _NOTE}, receipt_id)


def _person(repo: Any, cur: Any, operator: OperatorIdentity, receipt_id: str, payload: dict[str, Any],
            org_id: str) -> tuple[str, str | None, bool]:
    """The person's id, their email contact point (if any) and whether the person was created here."""
    email = payload.get("email")
    if email:
        cur.execute("select id::text, person_id::text from crm.contact_point where kind = 'email' and value_norm = %s",
                    (email,))
        row = cur.fetchone()
        if row is not None and row[1] is not None:
            person_id, contact_point_id = row[1], row[0]
            cur.execute("select 1 from crm.affiliation where person_id = %s::uuid and organization_id = %s::uuid "
                        "and valid_to is null limit 1", (person_id, org_id))
            if cur.fetchone() is None:
                cur.execute("select version from crm.person where id = %s::uuid", (person_id,))
                (version,) = cur.fetchone()
                authoring._handle_link_person_organization(repo, cur, operator, {
                    "person_id": person_id, "expected_version": version, "organization_id": org_id,
                    "role_title": payload.get("role"), "unit_label": payload.get("unit"), "note": _NOTE}, receipt_id)
            return person_id, contact_point_id, False
    name = payload.get("person_name") or payload["organization_name"]
    created = authoring._handle_create_person(repo, cur, operator, {
        "display_name": name, "email": email, "organization_id": org_id, "role_title": payload.get("role"),
        "note": _NOTE}, receipt_id)
    return created["person_id"], created.get("email_contact_point_id"), True


def _resolve_assertions(cur: Any, operator: OperatorIdentity, finding_id: str, targets: dict[str, tuple[str, str, bool]]) -> None:
    for kind, (resolved_kind, resolved_id, created) in targets.items():
        cur.execute(
            "update evidence.assertion set resolution = %s, resolved_kind = %s, resolved_id = %s::uuid, "
            "resolved_at = now(), resolved_by_operator_id = %s::uuid, updated_at = now() "
            "where source_record_id = %s::uuid and kind = %s and resolution = 'unresolved'",
            ("promoted" if created else "linked", resolved_kind, resolved_id, operator.operator_id, finding_id, kind))
    cur.execute("update evidence.source_record set review_status = 'promoted', updated_at = now() where id = %s::uuid",
                (finding_id,))


def contact_prospect(repo: Any, cur: Any, operator: OperatorIdentity, fields: dict[str, Any],
                     receipt_id: str) -> dict[str, Any]:
    finding = _lock_finding(cur, fields["finding_id"])
    payload = finding["payload"]
    previous = _latest_verdict(cur, finding["id"])
    if previous == "contactar":
        raise CommandRefused(409, "prospect_already_contacted", "Este prospecto ya está en el CRM.")
    c = classify(cur, email=payload.get("email"), organization_name=payload.get("organization_name") or "",
                 website=payload.get("website"))
    if c["state"] == "blocked":
        raise CommandRefused(409, "prospect_blocked", "Esta persona está en Bajas o rebotó; no se puede contactar.")
    org_id, org_created = _organization(repo, cur, operator, receipt_id, fields, payload, c["organization_candidates"])
    if not org_created:
        _ensure_prospect_role(repo, cur, operator, receipt_id, org_id)
    person_id, contact_point_id, person_created = _person(repo, cur, operator, receipt_id, payload, org_id)
    cur.execute("select id::text from crm.affiliation where person_id = %s::uuid and organization_id = %s::uuid "
                "order by created_at desc limit 1", (person_id, org_id))
    affiliation = cur.fetchone()
    targets: dict[str, tuple[str, str, bool]] = {"organization_name": ("organization", org_id, org_created)}
    if affiliation:
        targets["affiliation"] = ("affiliation", affiliation[0], person_created)
    if contact_point_id:
        targets["contact_address"] = ("contact_point", contact_point_id, person_created)
    _resolve_assertions(cur, operator, finding["id"], targets)
    source = payload.get("email_source_url") or next((url for _, url in payload.get("sources") or []), None)
    review_id, event_id = _record_verdict(repo, cur, operator, receipt_id, finding_id=finding["id"],
                                          verdict="contactar", previous=previous,
                                          reason=f"fuente: {source}"[:500] if source else None, remind_on=None)
    return {"command": CONTACT_PROSPECT, "finding_id": finding["id"], "review_id": review_id,
            "person_id": person_id, "organization_id": org_id, "contact_point_id": contact_point_id,
            "created": [name for name, made in (("organization", org_created), ("person", person_created)) if made]
            + ["prospect_review"], "event_ids": [event_id]}


class V2ProspectCommandRepository(V2CrmAuthoringRepository):
    """The «Prospectos» commands, with their own dispatch: the CRM authoring handlers are reached only
    inside `contact-prospect`'s transaction, never through this repository's routes."""

    _HANDLERS = {
        REVIEW_PROSPECT: review_prospect,
        CONTACT_PROSPECT: contact_prospect,
    }


__all__ = ["CONTACT_PROSPECT", "REVIEW_PROSPECT", "ContactProspectBody", "ReviewProspectBody",
           "V2ProspectCommandRepository", "contact_prospect", "prepare_contact_prospect",
           "prepare_review_prospect", "review_prospect"]
