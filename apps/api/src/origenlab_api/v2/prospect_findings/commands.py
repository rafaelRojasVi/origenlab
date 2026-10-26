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

`edit-prospect` (owner 2026-10-25: «let me edit everything — I found the person's email») records a
person's correction in `crm.prospect_edit` (#66): only the fields that differ from what the card
shows, a null clearing one. The finding is never rewritten; every read and `contact-prospect` apply
the corrections in order over its payload (`apply_edits`). A new email needs where it was found
(`email_source_note` or `email_source_url`) — the legal trail `contact-prospect` writes into its
verdict. Refused on a contacted finding (its data is in the CRM now): `prospect_already_contacted`;
with nothing changed: 422 `no_changes`. Event `source_record.prospect_edited`, payload `{fields}` —
the names of the changed fields, never their values.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import date, datetime
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from origenlab_api.v2 import crm_authoring as authoring
from origenlab_api.v2.commands import CommandRefused, as_uuid, normalize_email, validate_email_shape
from origenlab_api.v2.crm_authoring import V2CrmAuthoringRepository
from origenlab_api.v2.identity import OperatorIdentity
from origenlab_api.v2.prospect_findings.classify import classify, institution_domains

REVIEW_PROSPECT = "review-prospect"
CONTACT_PROSPECT = "contact-prospect"
EDIT_PROSPECT = "edit-prospect"

#: The finding fields a person may correct (the `prospect_edit_changes_keys` CHECK), with their limits.
TEXT_FIELDS: dict[str, int] = {
    "person_name": 200, "role": 200, "organization_name": 300, "unit": 300, "phone": 60,
    "why_now": 1000, "fund_label": 200, "amount_label": 200, "email_source_note": 300,
}
URL_FIELDS: tuple[str, ...] = ("email_source_url", "website", "linkedin_person", "linkedin_org", "orcid", "scholar")
EDITABLE: frozenset[str] = frozenset({*TEXT_FIELDS, *URL_FIELDS, "email", "equipment"})
_URL = re.compile(r"^https?://[^\s]+$", re.IGNORECASE)


def apply_edits(payload: dict[str, Any], edits: Iterable[dict[str, Any]] | None) -> dict[str, Any]:
    """The finding as a person last left it: every correction applied in order over the payload.
    A corrected email drops the finding's own record of where its email came from — that source
    named the old address; the correction carries its own (`email_source_note` / `_url`)."""
    out = dict(payload)
    for change in edits or []:
        if "email" in change:
            out.pop("email_source_year", None)
            out.pop("email_source_url", None)
            out.pop("email_source_note", None)
        out.update(change)
    return out
SANTIAGO = ZoneInfo("America/Santiago")
_NOTE = "Agregado desde «Prospectos»"


class ReviewProspectBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    finding_id: str
    verdict: Literal["mas_tarde", "descartar", "reabrir"]
    reason: Annotated[str, Field(max_length=500)] | None = None
    remind_on: date | None = None


class EditProspectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    finding_id: str
    #: Field → new value (null clears it). Unknown fields are refused.
    changes: dict[str, Any]


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


def _clean(field: str, value: Any) -> Any:
    """One edited value in its stored form, or a 422 naming the field."""
    if value is None:
        return None
    if field == "equipment":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise CommandRefused(422, "invalid_field", "Los equipos son una lista de textos.", details={"field": field})
        items = [v.strip() for v in value if v.strip()][:20]
        if any(len(v) > 120 for v in items):
            raise CommandRefused(422, "invalid_field", "Cada equipo tiene 120 caracteres como máximo.", details={"field": field})
        return items or None
    if not isinstance(value, str):
        raise CommandRefused(422, "invalid_field", "Ese campo es un texto.", details={"field": field})
    text = re.sub(r"[ \t]+", " ", value).strip()
    if not text:
        return None
    if field == "email":
        try:
            return validate_email_shape(normalize_email(text))
        except CommandRefused:
            raise CommandRefused(422, "invalid_field", "Ese correo no es válido.", details={"field": field}) from None
    if field in URL_FIELDS:
        if field != "email_source_url" and not _URL.match(text):
            raise CommandRefused(422, "invalid_field", "El enlace debe empezar con https://.", details={"field": field})
        if len(text) > 500:
            raise CommandRefused(422, "invalid_field", "El enlace es demasiado largo.", details={"field": field})
        return text
    if len(text) > TEXT_FIELDS[field]:
        raise CommandRefused(422, "invalid_field", f"Máximo {TEXT_FIELDS[field]} caracteres.", details={"field": field})
    return text


def prepare_edit_prospect(body: EditProspectBody) -> dict[str, Any]:
    unknown = sorted(set(body.changes) - EDITABLE)
    if unknown:
        raise CommandRefused(422, "invalid_field", "Ese campo no se puede editar.", details={"field": unknown[0]})
    changes = {k: _clean(k, v) for k, v in body.changes.items()}
    if "organization_name" in changes and not changes["organization_name"]:
        raise CommandRefused(422, "invalid_field", "La institución no puede quedar vacía.", details={"field": "organization_name"})
    return {"finding_id": as_uuid(body.finding_id, "finding_id"), "changes": changes}


def prepare_contact_prospect(body: ContactProspectBody) -> dict[str, Any]:
    return {"finding_id": as_uuid(body.finding_id, "finding_id"),
            "organization_id": as_uuid(body.organization_id, "organization_id") if body.organization_id else None,
            "create_new_organization": bool(body.create_new_organization)}


def _lock_finding(cur: Any, finding_id: str) -> dict[str, Any]:
    """The finding under a row lock, with every correction applied (`apply_edits`)."""
    cur.execute("select id::text, payload from evidence.source_record "
                "where id = %s::uuid and kind = 'prospect_finding' for update", (finding_id,))
    row = cur.fetchone()
    if row is None:
        raise CommandRefused(404, "prospect_not_found", "Ese prospecto no existe.")
    cur.execute("select changes from crm.prospect_edit where source_record_id = %s::uuid "
                "order by edited_at, id", (finding_id,))
    return {"id": row[0], "payload": apply_edits(row[1] or {}, [r[0] for r in cur.fetchall()])}


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
    # The institution's own domain: the email's (not free mail), else the website's — unless a live
    # institution already holds it (then it is not this one's to claim).
    domains = institution_domains(payload.get("email"), payload.get("website"))
    domain = domains[0] if domains else None
    if domain:
        cur.execute("select 1 from crm.organization_domain where domain_norm = %s and removed_at is null limit 1",
                    (domain,))
        if cur.fetchone() is not None:
            domain = None
    created = authoring._handle_register_organization(repo, cur, operator, {
        "name": payload["organization_name"], "legal_name": None, "kind": _kind_for(payload),
        "classification": "prospect", "product_lines": [], "note": _NOTE, "domain": domain,
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


def _institution_mailbox(repo: Any, cur: Any, operator: OperatorIdentity, receipt_id: str,
                         payload: dict[str, Any], org_id: str) -> tuple[str | None, bool]:
    """The lab's published address as the institution's shared mailbox; an address the CRM already
    holds is linked as it is. Returns the contact point and whether it was created here."""
    email = payload.get("email")
    if not email:
        return None, False
    cur.execute("select id::text from crm.contact_point where kind = 'email' and value_norm = %s", (email,))
    row = cur.fetchone()
    if row is not None:
        return row[0], False
    cur.execute("select version from crm.organization where id = %s::uuid", (org_id,))
    (version,) = cur.fetchone()
    made = authoring._handle_add_contact_point(repo, cur, operator, {
        "organization_id": org_id, "expected_version": version, "kind": "email", "value": email,
        "usage": "shared_mailbox", "note": _NOTE}, receipt_id)
    return made["contact_point_id"], True


def _resolve_assertions(cur: Any, operator: OperatorIdentity, finding_id: str, targets: dict[str, tuple[str, str, bool]]) -> None:
    for kind, (resolved_kind, resolved_id, created) in targets.items():
        cur.execute(
            "update evidence.assertion set resolution = %s, resolved_kind = %s, resolved_id = %s::uuid, "
            "resolved_at = now(), resolved_by_operator_id = %s::uuid, updated_at = now() "
            "where source_record_id = %s::uuid and kind = %s and resolution = 'unresolved'",
            ("promoted" if created else "linked", resolved_kind, resolved_id, operator.operator_id, finding_id, kind))
    cur.execute("update evidence.source_record set review_status = 'promoted', updated_at = now() where id = %s::uuid",
                (finding_id,))


def edit_prospect(repo: Any, cur: Any, operator: OperatorIdentity, fields: dict[str, Any],
                  receipt_id: str) -> dict[str, Any]:
    finding = _lock_finding(cur, fields["finding_id"])
    if _latest_verdict(cur, finding["id"]) == "contactar":
        raise CommandRefused(409, "prospect_already_contacted",
                             "Este prospecto ya está en el CRM: edítalo allí.")
    current = finding["payload"]
    changes = {k: v for k, v in fields["changes"].items() if current.get(k) != v}
    if not changes:
        raise CommandRefused(422, "no_changes", "No cambiaste nada.")
    if changes.get("email") and not (fields["changes"].get("email_source_note")
                                     or fields["changes"].get("email_source_url")):
        raise CommandRefused(422, "email_source_required", "Di dónde encontraste el correo.",
                             details={"field": "email_source_note"})
    if "email" in changes:  # the new address's source travels with it, even if unchanged in text
        for k in ("email_source_note", "email_source_url"):
            if k in fields["changes"]:
                changes[k] = fields["changes"][k]
    cur.execute("insert into crm.prospect_edit (source_record_id, changes, edited_by_operator_id) "
                "values (%s::uuid, %s::jsonb, %s::uuid) returning id::text",
                (finding["id"], json.dumps(changes, ensure_ascii=False), operator.operator_id))
    (edit_id,) = cur.fetchone()
    event_id = repo._append_event(
        cur, aggregate_kind="source_record", aggregate_id=finding["id"], event_type="source_record.prospect_edited",
        payload={"fields": sorted(changes)}, operator=operator, receipt_id=receipt_id)
    return {"command": EDIT_PROSPECT, "finding_id": finding["id"], "edit_id": edit_id,
            "fields": sorted(changes), "created": ["prospect_edit"], "event_ids": [event_id]}


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
    affiliation = None
    if payload.get("person_name"):
        person_id, contact_point_id, person_created = _person(repo, cur, operator, receipt_id, payload, org_id)
        cur.execute("select id::text from crm.affiliation where person_id = %s::uuid and organization_id = %s::uuid "
                    "order by created_at desc limit 1", (person_id, org_id))
        affiliation = cur.fetchone()
    else:
        # A lab with no named person: no person is invented; its address is the institution's mailbox.
        person_id, person_created = None, False
        contact_point_id, mailbox_created = _institution_mailbox(repo, cur, operator, receipt_id, payload, org_id)
    targets: dict[str, tuple[str, str, bool]] = {"organization_name": ("organization", org_id, org_created)}
    if affiliation:
        targets["affiliation"] = ("affiliation", affiliation[0], person_created)
    if contact_point_id:
        made = person_created if payload.get("person_name") else mailbox_created
        targets["contact_address"] = ("contact_point", contact_point_id, made)
    _resolve_assertions(cur, operator, finding["id"], targets)
    source = (payload.get("email_source_url") or payload.get("email_source_note")
              or next((url for _, url in payload.get("sources") or []), None))
    review_id, event_id = _record_verdict(repo, cur, operator, receipt_id, finding_id=finding["id"],
                                          verdict="contactar", previous=previous,
                                          reason=f"fuente: {source}"[:500] if source else None, remind_on=None)
    return {"command": CONTACT_PROSPECT, "finding_id": finding["id"], "review_id": review_id,
            "person_id": person_id, "organization_id": org_id, "contact_point_id": contact_point_id,
            "created": [name for name, made in (("organization", org_created), ("person", person_created)) if made]
            + ["prospect_review"], "event_ids": [event_id]}


PREPARE_PROSPECT_EMAIL = "prepare-prospect-email"

#: Labels applied to the drafted message (gmail.modify scope required).
_DRAFT_LABELS = ["Claude – revisar", "Prospecto"]  # «Claude – revisar»: the label the owner reviews drafts by

DRAFT_FAILURE_ES: dict[str, str] = {
    "token_refresh_failed": "No se pudo crear el borrador: el permiso de Gmail venció o fue revocado.",
    "draft_creation_failed": "Gmail rechazó la creación del borrador.",
    "label_list_failed": "No se pudo leer las etiquetas de Gmail.",
    "label_create_failed": "No se pudo crear la etiqueta en Gmail.",
    "label_apply_failed": "No se pudo etiquetar el borrador en Gmail.",
    "network": "No se pudo contactar a Gmail.",
}


class PrepareProspectEmailBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: str
    again: bool = False  # a second draft for a finding that already has one

    @property
    def finding_uuid(self) -> str:
        return as_uuid(self.finding_id, "finding_id")


def prepare_prepare_prospect_email(body: PrepareProspectEmailBody) -> dict[str, Any]:
    return {"finding_id": body.finding_uuid}


class V2ProspectCommandRepository(V2CrmAuthoringRepository):
    """The «Prospectos» commands, with their own dispatch: the CRM authoring handlers are reached only
    inside `contact-prospect`'s transaction, never through this repository's routes."""

    _HANDLERS = {
        REVIEW_PROSPECT: review_prospect,
        CONTACT_PROSPECT: contact_prospect,
        EDIT_PROSPECT: edit_prospect,
    }

    def __init__(self, connect: Any, dsn: str, *,
                 sender: Any = None,
                 statement_timeout_ms: int = 15_000) -> None:
        super().__init__(connect, dsn, statement_timeout_ms)
        self._sender = sender  # GmailSender | None

    # ------------------------------------------------------------------ prepare-prospect-email

    def prepare_email(
        self,
        *,
        operator: OperatorIdentity,
        body: "PrepareProspectEmailBody",
        idempotency_key: str,
        digest: str,
    ) -> dict[str, Any]:
        """«Preparar correo»: create a Gmail draft for a prospect, never send it.

        1. One transaction: claim the receipt, lock the finding, refuse (no email, hidden, Bajas,
           already drafted without `again`), run «Contactar» if it is still pending, build the
           message — committed, so the person is in the CRM whatever Gmail answers;
        2. Gmail, outside any transaction: the draft, then the labels (best-effort);
        3. One transaction: the `prospect_email_drafted` event and the completed receipt.

        A Gmail failure finishes the receipt `failed` (502); a retry with a new key drafts again.
        """
        from origenlab_api.v2.gmail_send import GmailSendError
        from origenlab_api.v2.prospect_findings.intro_email import (
            build_html,
            build_mime_message,
            build_subject,
            build_text,
            load_catalogue_pdf,
        )

        if self._sender is None:
            raise CommandRefused(503, "gmail_not_configured",
                                 "Gmail no está conectado en este servidor; no se puede crear el borrador.")
        finding_id = body.finding_uuid
        contacted = False
        with self._write() as cur:
            receipt_id, replay = self._claim_receipt(cur, operator, idempotency_key,
                                                     PREPARE_PROSPECT_EMAIL, digest)
            if replay is not None:
                return replay
            finding = _lock_finding(cur, finding_id)  # 404 rolls back the claim
            payload = finding["payload"]
            if payload.get("hidden_reason"):
                raise CommandRefused(404, "prospect_not_found", "Ese prospecto no existe.")
            if not payload.get("email"):
                raise CommandRefused(409, "prospect_no_email",
                                     "Este prospecto no tiene correo; agrégalo con «Editar».")
            c = classify(cur, email=payload.get("email"), organization_name=payload.get("organization_name") or "",
                         website=payload.get("website"))
            if c["state"] == "blocked":
                raise CommandRefused(409, "prospect_blocked",
                                     "Esta persona está en Bajas o rebotó; no se le escribe.")
            cur.execute("select 1 from crm.domain_event where aggregate_kind = 'source_record' "
                        "and aggregate_id = %s::uuid and event_type = 'source_record.prospect_email_drafted' "
                        "limit 1", (finding_id,))
            if cur.fetchone() is not None and not body.again:
                raise CommandRefused(409, "prospect_email_already_drafted",
                                     "Ya hay un borrador para este prospecto en Gmail.")
            if _latest_verdict(cur, finding_id) != "contactar":
                try:
                    contact_prospect(self, cur, operator, {"finding_id": finding_id, "organization_id": None,
                                                           "create_new_organization": False}, receipt_id)
                except CommandRefused as exc:
                    if exc.code != "organization_ambiguous":
                        raise
                    raise CommandRefused(409, "prospect_contact_first",
                                         "Hay varias instituciones posibles: usa «Contactar» para elegirla "
                                         "y luego prepara el correo.") from exc
                contacted = True
            email_addr: str = payload["email"]
            signature_html = self._sender.fetch_send_as_signature()
            fields = {k: payload.get(k) for k in ("person_name", "salutation_sex", "project_title", "fund_label",
                                                  "email_source_note", "email_source_url", "email_source_year")}
            fields["equipment"] = list(payload.get("equipment") or [])
            subject = build_subject(payload.get("fund_label"))
            try:
                raw = build_mime_message(
                    to=email_addr, subject=subject,
                    text_body=build_text(**fields, gmail_signature_html=signature_html),
                    html_body=build_html(**fields, gmail_signature_html=signature_html),
                    pdf_bytes=load_catalogue_pdf())
            except ValueError as exc:  # rolls the claim and the contact back
                raise CommandRefused(422, "invalid_recipient", "El correo de este prospecto no es válido.") from exc

        try:
            draft_id, message_id = self._sender.create_draft(raw)
        except GmailSendError as exc:
            _finish_receipt(self, receipt_id, 502, {"error": exc.kind, "detail": exc.detail,
                                                    "contacted": contacted})
            raise CommandRefused(502, exc.kind,
                                 DRAFT_FAILURE_ES.get(exc.kind, "No se pudo crear el borrador.")) from exc
        try:
            self._sender.apply_labels(message_id, _DRAFT_LABELS)
            labels_applied = True
        except GmailSendError:
            labels_applied = False  # gmail.modify not granted yet: the draft stands without labels

        with self._write() as cur:
            event_id = self._append_event(
                cur, aggregate_kind="source_record", aggregate_id=finding_id,
                event_type="source_record.prospect_email_drafted",
                payload={"draft_id": draft_id, "message_id": message_id, "labels_applied": labels_applied,
                         "signature": signature_html is not None, "subject": subject},
                operator=operator, receipt_id=receipt_id)
            response: dict[str, Any] = {
                "command": PREPARE_PROSPECT_EMAIL, "finding_id": finding_id, "draft_id": draft_id,
                "message_id": message_id, "gmail_url": gmail_draft_url(message_id), "to": email_addr,
                "subject": subject, "signature": signature_html is not None, "labels_applied": labels_applied,
                "contacted": contacted, "event_ids": [event_id], "idempotency_key": idempotency_key,
                "command_receipt_id": receipt_id, "replayed": False,
            }
            self._complete_receipt(cur, receipt_id, response)
        return response


def gmail_draft_url(message_id: str) -> str:
    """The draft open in Gmail's compose window (contacto@ is the first signed-in account)."""
    return f"https://mail.google.com/mail/u/0/#drafts?compose={message_id}"

def _finish_receipt(repo: V2ProspectCommandRepository, receipt_id: str, status_code: int,
                    response: dict[str, Any]) -> None:
    from origenlab_api.v2.command_core import json_payload
    with repo._write() as cur:  # noqa: SLF001
        cur.execute(
            "update platform.command_receipt set status = %s, response_status = %s, "
            "response_body = %s::jsonb, completed_at = now() where id = %s and status = 'in_progress'",
            ("failed", status_code, json_payload(response), receipt_id),
        )


__all__ = [
    "CONTACT_PROSPECT", "EDIT_PROSPECT", "PREPARE_PROSPECT_EMAIL", "REVIEW_PROSPECT",
    "ContactProspectBody", "EditProspectBody", "PrepareProspectEmailBody", "ReviewProspectBody",
    "V2ProspectCommandRepository", "apply_edits", "contact_prospect", "edit_prospect",
    "prepare_contact_prospect", "prepare_edit_prospect", "prepare_prepare_prospect_email",
    "prepare_review_prospect", "review_prospect",
]
