"""Forwarded requests become Solicitudes (spec 2026-10-14; owner decisions of that day).

Tatiana forwards to contacto@ requests that reached her elsewhere. The worker reads who really asked
and stores it as an `evidence.source_record` of kind `forwarded_request` (customer fields and the id
of the email they came from — never its text). Here:

* `read_forwarded_requests` — the pending ones for Hoy, the «RV:» and «Fwd:» copies of one request
  collapsed into one row (same customer email, same request once the forward prefixes are dropped).
  A request handled or dismissed through any of its copies is gone.
* `create_request_from_forward` — «Crear solicitud»: one transaction opens a case at `lead` whose
  origin is the forwarded_request record, names its requesting institution (an existing one, or a
  new one «por confirmar»; none for a free-mail customer with no company), writes the customer's
  details as a note, links the email only when it is ours (contacto@origenlab.cl) — **never a
  Labdelivery-mailbox email** — and marks every copy `promoted`.
* `dismiss_forwarded_request` — «Descartar»: every copy `rejected`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from origenlab_api.v2.commands import CommandRefused
from origenlab_api.v2.identity import OperatorIdentity

from origenlab_api.v2.case_commands import CREATE_REQUEST_FROM_FORWARD, DISMISS_FORWARDED_REQUEST  # noqa: E402

_PREFIX = re.compile(r"^\s*(?:(?:re|rv|fw|fwd|tr|aw|wg|sv|reenv(?:iado)?)\s*:\s*)+", re.IGNORECASE)

READ_SQL = """
select f.id::text as id, f.review_status, f.created_at, f.payload,
       g.payload->>'gmail_message_id' as gmail_message_id, g.payload->>'sent_at' as sent_at,
       exists (select 1 from crm.opportunity_evidence oe where oe.source_record_id = g.id
                and oe.unlinked_at is null) as linked
  from evidence.source_record f
  left join evidence.source_record g on g.id::text = f.payload->>'forwarded_message_id'
 where f.kind = 'forwarded_request' and f.created_at > now() - interval '60 days'
 order by f.created_at desc
 limit 500
"""


def _payload(row: Mapping[str, Any]) -> dict[str, Any]:
    p = row["payload"]
    return json.loads(p) if isinstance(p, str) else dict(p or {})


def _key(payload: Mapping[str, Any]) -> tuple[str, str]:
    item = _PREFIX.sub("", str(payload.get("requested_item") or payload.get("subject") or "")).strip().casefold()
    return str(payload.get("original_email") or "").lower(), re.sub(r"\s+", " ", item)


def group_forwards(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Pending requests, one row per (customer email, request), newest first. Pure."""
    from origenlab_api.v2.crm_workspace import gmail_url

    groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for r in rows:
        groups.setdefault(_key(_payload(r)), []).append(r)
    out = []
    for members in groups.values():
        if any(m["review_status"] != "pending" or m.get("linked") for m in members):
            continue  # handled or dismissed through one of its copies, or its email is already on a case
        ours = [m for m in members if _payload(m).get("origin") != "labdelivery"]
        lead = max(ours or members, key=lambda m: str(m.get("sent_at") or m.get("created_at") or ""))
        p = _payload(lead)
        out.append({
            "id": lead["id"],
            "ids": sorted(m["id"] for m in members),
            "received_at": max(str(m.get("sent_at") or m.get("created_at") or "") for m in members) or None,
            "customer": {"name": p.get("original_name"), "email": p.get("original_email"),
                         "company": p.get("company"), "rut": p.get("rut"), "phone": p.get("phone")},
            "requested_item": _PREFIX.sub("", str(p.get("requested_item") or "")).strip() or None,
            "relay": p.get("relay"),
            "origins": sorted({str(_payload(m).get("origin")) for m in members}),
            # Only our own copy is ever opened from here; a Labdelivery-mailbox forward has no link.
            "gmail_url": gmail_url(lead.get("gmail_message_id")) if ours else None,
        })
    out.sort(key=lambda i: i["received_at"] or "", reverse=True)
    return out


def read_forwarded_requests(cur: Any) -> list[dict[str, Any]]:
    from origenlab_api.v2.mail_rules import FREE_MAIL_DOMAINS

    cur.execute(READ_SQL)
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    for r in rows:
        r["created_at"] = r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else r["created_at"]
    items = group_forwards(rows)
    # An institution the CRM already holds for the customer's email domain (never a free-mail one).
    for it in items:
        email = it["customer"]["email"] or ""
        domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
        it["institution"] = None
        if domain and domain not in FREE_MAIL_DOMAINS:
            cur.execute(
                "select o.id::text, o.name, o.version from crm.organization_domain d "
                "join crm.organization o on o.id = d.organization_id "
                "where d.domain_norm = %s and d.removed_at is null and o.merged_into_organization_id is null and o.status = 'active' "
                "order by o.created_at limit 2", (domain,))
            found = cur.fetchall()
            if len(found) == 1:
                it["institution"] = {"id": found[0][0], "name": found[0][1], "version": int(found[0][2])}
    return items


# ──────────────────────────────────────────────────────────── commands ──

def _group_for(cur: Any, forward_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cur.execute("select id::text as id, kind, review_status, payload from evidence.source_record "
                "where id = %s for update", (forward_id,))
    row = cur.fetchone()
    if row is None or row[1] != "forwarded_request":
        raise CommandRefused(404, "forwarded_request_not_found", "no such forwarded request")
    lead = {"id": row[0], "review_status": row[2], "payload": row[3]}
    if lead["review_status"] != "pending":
        raise CommandRefused(409, "forwarded_request_handled", "this forwarded request was already handled")
    key = _key(_payload(lead))
    cur.execute("select id::text, review_status, payload from evidence.source_record "
                "where kind = 'forwarded_request' and review_status = 'pending' and id <> %s "
                "and lower(payload->>'original_email') = %s for update", (lead["id"], key[0]))
    siblings = [{"id": i, "review_status": s, "payload": p} for i, s, p in cur.fetchall()
                if _key(_payload({"payload": p})) == key]
    return lead, [lead, *siblings]


def _mark(cur: Any, ids: Sequence[str], status: str) -> None:
    cur.execute("update evidence.source_record set review_status = %s, updated_at = now() "
                "where id = any(%s::uuid[]) and review_status = 'pending'", (status, list(ids)))


def create_request_from_forward(repo: Any, cur: Any, operator: OperatorIdentity, fields: dict[str, Any],
                                receipt_id: str) -> dict[str, Any]:
    from origenlab_api.v2.mail_rules_repository import register_machine_organization

    lead, members = _group_for(cur, fields["forwarded_request_id"])
    p = _payload(lead)
    note = fields["note"]
    item = _PREFIX.sub("", str(p.get("requested_item") or p.get("subject") or "")).strip() or "solicitud reenviada"
    opened = repo._open_commercial_case(cur, operator, {
        "title": f"Solicitud — {item}"[:200], "origin_source_record_id": lead["id"], "note": note}, receipt_id)
    case_id, version = opened["opportunity_id"], int(opened["opportunity_version"])

    organization_id, organization_created = None, False
    if fields.get("organization_id") is not None:
        organization_id, organization_version = fields["organization_id"], int(fields["organization_version"])
    elif fields.get("new_organization_name"):
        name = fields["new_organization_name"]
        cur.execute("select id::text, version from crm.organization where status = 'active' "
                    "and merged_into_organization_id is null and lower(name) = lower(%s) order by created_at limit 2",
                    (name,))
        same = cur.fetchall()
        if len(same) == 1:
            organization_id, organization_version = same[0][0], int(same[0][1])
        else:
            made = register_machine_organization(repo, cur, operator, {
                "name": name, "domain": None, "origin_source_record_id": lead["id"], "note": note}, receipt_id)
            organization_id, organization_version = made["organization_id"], int(made["organization_version"])
            organization_created = True
    if organization_id is not None:
        added = repo._add_case_organization(cur, operator, {
            "opportunity_id": case_id, "opportunity_version": version, "organization_id": organization_id,
            "organization_version": organization_version, "role": "requesting_institution",
            "supplier_exception_reason": None, "note": note}, receipt_id)
        version = int(added["opportunity_version"])

    links = _link_our_copies(repo, cur, operator, case_id, version, members, note, receipt_id)
    _customer_note(repo, cur, operator, case_id, p, members, receipt_id)
    _mark(cur, [m["id"] for m in members], "promoted")
    return {"command": CREATE_REQUEST_FROM_FORWARD, "opportunity_id": case_id, "opportunity_version": version,
            "organization_id": organization_id, "organization_created": organization_created,
            "linked_emails": len(links), "opportunity_evidence_ids": links,
            "forwarded_request_ids": sorted(m["id"] for m in members)}


def attach_forward_to_case(repo: Any, cur: Any, operator: OperatorIdentity, fields: dict[str, Any],
                           receipt_id: str) -> dict[str, Any]:
    """R11 (`mail_rules.py`): a forwarded request for a customer who already has an open case joins
    that case — our own copies linked, the customer's details as a note, every copy `promoted`."""
    lead, members = _group_for(cur, fields["forwarded_request_id"])
    case_id = str(fields["opportunity_id"])
    cur.execute("select version, closed_at is not null from crm.opportunity where id = %s for update", (case_id,))
    row = cur.fetchone()
    if row is None:
        raise CommandRefused(404, "case_not_found", "no such commercial case")
    if row[1]:
        raise CommandRefused(409, "case_is_closed", "a forwarded request joins an open case only")
    links = _link_our_copies(repo, cur, operator, case_id, int(row[0]), members, fields["note"], receipt_id)
    _customer_note(repo, cur, operator, case_id, _payload(lead), members, receipt_id)
    _mark(cur, [m["id"] for m in members], "promoted")
    return {"command": "attach_forward_to_case", "opportunity_id": case_id, "opportunity_version": int(row[0]),
            "opportunity_evidence_ids": links, "forwarded_request_ids": sorted(m["id"] for m in members)}


def _link_our_copies(repo: Any, cur: Any, operator: OperatorIdentity, case_id: str, version: int,
                     members: Sequence[Mapping[str, Any]], note: str, receipt_id: str) -> list[str]:
    """The emails it was read from: only our own copies, never the Labdelivery mailbox's."""
    links = []
    for m in members:
        mp = _payload(m)
        if mp.get("origin") == "labdelivery" or not mp.get("forwarded_message_id"):
            continue
        linked = repo._link_case_evidence(cur, operator, {
            "opportunity_id": case_id, "opportunity_version": version, "source_record_id": mp["forwarded_message_id"],
            "relation": "mentions", "note": note}, receipt_id)
        links.append(linked["opportunity_evidence_id"])
    return links


def _customer_note(repo: Any, cur: Any, operator: OperatorIdentity, case_id: str, p: Mapping[str, Any],
                   members: Sequence[Mapping[str, Any]], receipt_id: str) -> None:
    details = [f"Solicitud reenviada por correo ({', '.join(sorted({str(_payload(m).get('origin')) for m in members}))})."]
    for label, key in (("Contacto", "original_name"), ("Correo", "original_email"), ("Institución", "company"),
                       ("RUT", "rut"), ("Teléfono", "phone"), ("Pide", "requested_item")):
        if p.get(key):
            details.append(f"{label}: {p[key]}")
    cur.execute("insert into crm.note (subject_kind, subject_id, body, author_operator_id, revision_no, status) "
                "values ('opportunity', %s::uuid, %s, %s::uuid, 1, 'active') returning id::text",
                (case_id, "\n".join(details), operator.operator_id))
    note_id = cur.fetchone()[0]
    repo._append_event(cur, aggregate_kind="note", aggregate_id=note_id, event_type="note.created",
                       payload={"subject_kind": "opportunity", "subject_id": case_id},
                       operator=operator, receipt_id=receipt_id)


def set_forward_status(cur: Any, ids: Sequence[str], status: str) -> None:
    """«Quitar» on an R11 action: back to `pending` (a joined case was wrong) or `rejected` (not a request)."""
    cur.execute("update evidence.source_record set review_status = %s, updated_at = now() "
                "where id = any(%s::uuid[]) and kind = 'forwarded_request'", (status, list(ids)))


def dismiss_forwarded_request(repo: Any, cur: Any, operator: OperatorIdentity, fields: dict[str, Any],
                              receipt_id: str) -> dict[str, Any]:
    _, members = _group_for(cur, fields["forwarded_request_id"])
    _mark(cur, [m["id"] for m in members], "rejected")
    return {"command": DISMISS_FORWARDED_REQUEST, "forwarded_request_ids": sorted(m["id"] for m in members)}
