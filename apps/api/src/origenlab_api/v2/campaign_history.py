"""Campaign history reads — the Marketing campaign detail's Resumen, Destinatarios, Respuestas and
Auditoría tabs, and the totals on every campaign card.

Read only, from PostgreSQL V2 (``outbound.*``, ``crm.*``, ``evidence.*``, ``comms.*``). Nothing here
reads the V1 SQLite, Gmail or Drive, and nothing is inferred.

**One recipient base, one predicate per total.** Every total a card or the summary shows is a
count over :data:`RECIPIENT_BASE_SQL` filtered by the predicate in :data:`TOTALS`, and the
recipient list for that total is the same base filtered by the same predicate. A total and the
rows it opens therefore agree by construction — the test suite checks it on real-shaped data.

**Recipients are not attempts, and an accepted attempt is not a delivery.** A recipient is one
row of ``outbound.campaign_recipient``; ``sent`` counts recipients with at least one attempt Gmail
*accepted*. Attempts are counted separately. A delivery is confirmed only by
``delivery_state = 'sent_copy_confirmed'``; ``pending`` is not a delivery.

**Search never becomes an address oracle.** An operator who may not see addresses (``viewer``)
searches only the names the CRM holds (person, institution); the address column is not part of
the predicate at all, so a result count cannot reveal whether an address is in the audience. The
list is never ordered by address either: position would leak the masked local part's order.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from origenlab_api.v2.contact_redaction import sees_contact_addresses
from origenlab_api.v2.read_transaction import Statement, run_together

GMAIL_MESSAGE_URL = "https://mail.google.com/mail/u/0/#all/{}"

#: The address blocks an exclusion reason stands for (a block, not a policy or manual decision).
BLOCK_REASONS = ("block", "block_domain", "precheck_block")

#: Every total, as a predicate over the recipient base aliased ``b``. The single source for the
#: card totals, the summary and the recipient filters.
TOTALS: dict[str, str] = {
    "audience": "true",
    "included": "b.state <> 'excluded'",
    "sent": "b.accepted > 0",
    "excluded": "b.state = 'excluded'",
    "blocked": "b.state = 'excluded' and b.exclusion_reasons && array['block', 'block_domain', 'precheck_block']::text[]",
    "unsent": "b.state <> 'excluded' and b.accepted = 0",
    "rejected": "b.rejected > 0",
    "bounced": "(b.state = 'bounced' or b.bounced_attempts > 0)",
    "responses": "b.replies > 0",
}

TOTAL_LABEL = {
    "audience": "Audiencia",
    "included": "Incluidos",
    "sent": "Enviados (aceptados por Gmail)",
    "excluded": "Excluidos",
    "blocked": "Bloqueados",
    "unsent": "Incluidos, no enviados",
    "rejected": "Rechazados",
    "bounced": "Rebotados",
    "responses": "Con respuesta",
}

EXCLUSION_LABEL = {
    "block": "Dirección bloqueada",
    "block_domain": "Dominio bloqueado",
    "precheck_block": "Bloqueada en la verificación previa",
    "prior_contact": "Contacto previo",
    "cooldown": "En período de espera",
    "policy_supplier": "Proveedor",
    "policy_no_channel": "Sin canal de contacto",
    "precheck_switch": "Envío deshabilitado en la verificación previa",
    "invalid_address": "Dirección no válida",
    "policy_internal_domain": "Dominio interno",
    "prior_reply": "Respondió antes",
    "policy_noise_address": "Dirección de ruido",
    "policy_noise_organization": "Institución de ruido",
    "manual_inactive": "Marcada inactiva por un operador (V1)",
    "manual_hold": "Retenida por un operador",
    "already_in_audience": "Ya estaba en la audiencia",
}

REPLY_CLASS_LABEL = {
    "human_reply": "Respuesta",
    "auto_reply": "Respuesta automática",
    "ndr": "Aviso de no entrega",
    "unsubscribe_request": "BAJA / REMOVER",
    "complaint": "Queja",
    "not_a_reply": "No es respuesta",
    "unclassified": "Sin clasificar",
}

REPLIES_NOT_SYNCED = "Respuestas no sincronizadas desde Gmail"

#: One row per campaign recipient with its attempt and reply aggregates. ``%(campaign)s`` is the
#: only parameter; ``{where}`` is filled from :data:`TOTALS` and the validated filters only.
RECIPIENT_BASE_SQL = """
select r.id, r.campaign_id, r.address_norm, r.state, r.exclusion_reasons,
       r.person_id, r.organization_id, r.contact_point_id, r.created_at,
       coalesce(a.attempts, 0) as attempts, coalesce(a.accepted, 0) as accepted,
       coalesce(a.rejected, 0) as rejected, coalesce(a.bounced, 0) as bounced_attempts,
       coalesce(a.confirmed, 0) as confirmed,
       a.first_accepted_at, a.last_accepted_at, a.last_attempt_created_at,
       a.error_class, a.bounce_class, a.provider_message_id,
       coalesce(rp.replies, 0) as replies, rp.last_reply_at
  from outbound.campaign_recipient r
  left join lateral (
    select count(*) as attempts,
           count(*) filter (where s.submission_state = 'accepted') as accepted,
           count(*) filter (where s.submission_state = 'rejected') as rejected,
           count(*) filter (where s.delivery_state = 'bounced') as bounced,
           count(*) filter (where s.delivery_state = 'sent_copy_confirmed') as confirmed,
           min(s.accepted_at) as first_accepted_at,
           max(s.accepted_at) as last_accepted_at,
           max(s.created_at) as last_attempt_created_at,
           (array_agg(s.error_class order by s.created_at desc)
              filter (where s.submission_state = 'rejected'))[1] as error_class,
           (array_agg(s.bounce_class order by s.created_at desc)
              filter (where s.delivery_state = 'bounced'))[1] as bounce_class,
           (array_agg(s.provider_message_id order by s.accepted_at desc nulls last)
              filter (where s.provider_message_id is not null))[1] as provider_message_id
      from outbound.send_attempt s
     where s.campaign_recipient_id = r.id
  ) a on true
  left join lateral (
    select count(*) as replies, max(cr.received_at) as last_reply_at
      from outbound.campaign_reply cr
     where cr.campaign_recipient_id = r.id
  ) rp on true
"""


def _iso(value: Any) -> str | None:
    return None if value is None else value.isoformat() if hasattr(value, "isoformat") else str(value)


def gmail_url(provider_message_id: str | None) -> str | None:
    """A Gmail link only when a provider id is already stored. Gmail is never called."""
    return GMAIL_MESSAGE_URL.format(provider_message_id) if provider_message_id else None


def shown_address(address: str | None, role: str | None) -> str | None:
    """The address as recorded for a role that may read it; otherwise only its domain.

    Masked here, in the read, and not only by the route class's value-based walk: a history row is
    nothing but an address, and one mask that misses a character class would expose it.
    """
    if address is None or sees_contact_addresses(role):
        return address
    return "***@" + address.rpartition("@")[2]


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def outcome(row: Mapping[str, Any]) -> str:
    """One display outcome per recipient. The totals, not this, are what the filters count."""
    if row["state"] == "excluded":
        return "excluded"
    if row["state"] == "bounced" or row["bounced_attempts"]:
        return "bounced"
    if row["accepted"]:
        return "sent"
    if row["rejected"]:
        return "rejected"
    return "unsent"


# --------------------------------------------------------------------------- totals


def totals_sql(scope: str) -> str:
    """Every total for one campaign (``scope = 'one'``) or per campaign (``'all'``)."""
    counts = ",\n       ".join(f"count(*) filter (where {pred})::int as {key}" for key, pred in TOTALS.items())
    where = "where r.campaign_id = %(campaign)s" if scope == "one" else ""
    group = "" if scope == "one" else "group by b.campaign_id"
    head = "" if scope == "one" else "b.campaign_id::text as campaign_id,\n       "
    return f"select {head}{counts}\n  from ({RECIPIENT_BASE_SQL} {where}) b\n {group}"


ATTEMPTS_SQL = """
select s.campaign_id::text as campaign_id,
       count(*)::int as attempts,
       count(*) filter (where s.submission_state = 'accepted')::int as accepted,
       count(*) filter (where s.submission_state = 'rejected')::int as rejected,
       count(*) filter (where s.submission_state not in ('accepted', 'rejected'))::int as other,
       count(*) filter (where s.delivery_state = 'sent_copy_confirmed')::int as delivery_confirmed,
       count(*) filter (where s.delivery_state = 'pending')::int as delivery_pending,
       count(*) filter (where s.delivery_state = 'bounced')::int as delivery_bounced,
       count(*) filter (where s.submission_state = 'rejected' and s.accepted_at is null
                          and s.dispatch_started_at is null)::int as rejected_undated,
       count(*) filter (where s.provider_message_id is not null)::int as with_provider_id,
       to_char(min(s.accepted_at) at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as first_accepted_at,
       to_char(max(s.accepted_at) at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"') as last_accepted_at
  from outbound.send_attempt s
 where s.campaign_id is not null {filter}
 group by s.campaign_id
"""

EMPTY_ATTEMPTS = {
    "attempts": 0, "accepted": 0, "rejected": 0, "other": 0, "delivery_confirmed": 0,
    "delivery_pending": 0, "delivery_bounced": 0, "rejected_undated": 0, "with_provider_id": 0,
    "first_accepted_at": None, "last_accepted_at": None,
}


def all_totals_statements() -> list[Statement]:
    """The two independent reads behind `read_all_totals`, for a caller to batch."""
    return [(totals_sql("all"), None), (ATTEMPTS_SQL.format(filter=""), None)]


def read_all_totals(cur: Any) -> dict[str, dict[str, Any]]:
    """``{campaign_id: {"totals": {...}, "attempts": {...}}}`` for every campaign with a row."""
    return all_totals_from(run_together(cur, all_totals_statements()))


def all_totals_from(cursors: list[Any]) -> dict[str, dict[str, Any]]:
    """`read_all_totals` from the executed cursors of `all_totals_statements`."""
    totals_cur, attempts_cur = cursors
    cols = [d[0] for d in totals_cur.description]
    out: dict[str, dict[str, Any]] = {}
    for row in totals_cur.fetchall():
        rec = dict(zip(cols, row, strict=True))
        cid = rec.pop("campaign_id")
        out[cid] = {"totals": rec, "attempts": dict(EMPTY_ATTEMPTS)}
    cols = [d[0] for d in attempts_cur.description]
    for row in attempts_cur.fetchall():
        rec = dict(zip(cols, row, strict=True))
        cid = rec.pop("campaign_id")
        out.setdefault(cid, {"totals": {k: 0 for k in TOTALS}, "attempts": dict(EMPTY_ATTEMPTS)})["attempts"] = rec
    return out


def read_totals(cur: Any, campaign_id: str) -> dict[str, Any]:
    cur.execute(totals_sql("one"), {"campaign": campaign_id})
    cols = [d[0] for d in cur.description]
    totals = dict(zip(cols, cur.fetchone(), strict=True))
    cur.execute(ATTEMPTS_SQL.format(filter="and s.campaign_id = %(campaign)s"), {"campaign": campaign_id})
    cols = [d[0] for d in cur.description]
    row = cur.fetchone()
    attempts = dict(EMPTY_ATTEMPTS)
    if row:
        attempts.update(dict(zip(cols, row, strict=True)))
        attempts.pop("campaign_id", None)
    return {"totals": totals, "attempts": attempts}


def replies_state(total_replies: int, baja_with_lineage: int) -> dict[str, Any]:
    """Whether this campaign's replies are known at all. Zero stored replies is not "no replies":
    nothing synchronizes Gmail, so the honest answer is that they were never synchronized."""
    if total_replies == 0 and baja_with_lineage == 0:
        return {"state": "not_synced", "label": REPLIES_NOT_SYNCED, "count": None}
    return {
        "state": "partial",
        "label": "Respuestas registradas por lotes aplicados a mano; Gmail no se sincroniza automáticamente.",
        "count": total_replies,
    }


# --------------------------------------------------------------------------- recipients


class RecipientQuery:
    """Validated recipient filters. Built by the route; never from raw SQL."""

    MAX_PAGE_SIZE = 100

    def __init__(
        self,
        *,
        total: str = "audience",
        reason: str | None = None,
        identity: str | None = None,
        q: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> None:
        if total not in TOTALS:
            raise ValueError("unknown total")
        if reason is not None and reason not in EXCLUSION_LABEL:
            raise ValueError("unknown exclusion reason")
        if identity not in (None, "crm_person", "historical_address"):
            raise ValueError("unknown identity filter")
        if page < 1 or not 1 <= page_size <= self.MAX_PAGE_SIZE:
            raise ValueError("page out of range")
        self.total = total
        self.reason = reason
        self.identity = identity
        self.q = (q or "").strip() or None
        self.page = page
        self.page_size = page_size

    def where(self, role: str | None) -> tuple[str, dict[str, Any]]:
        clauses = [TOTALS[self.total]]
        params: dict[str, Any] = {}
        if self.reason:
            clauses.append("%(reason)s = any(b.exclusion_reasons)")
            params["reason"] = self.reason
        if self.identity == "crm_person":
            clauses.append("b.person_id is not null")
        elif self.identity == "historical_address":
            clauses.append("b.person_id is null")
        if self.q:
            params["q"] = _like(self.q)
            names = "pe.display_name ilike %(q)s or org.name ilike %(q)s"
            # The address is searchable only by a role that may read it (see the module docstring).
            clauses.append(f"(b.address_norm ilike %(q)s or {names})" if sees_contact_addresses(role) else f"({names})")
        return " and ".join(f"({c})" for c in clauses), params

    def as_dict(self, role: str | None) -> dict[str, Any]:
        return {
            "total": self.total, "reason": self.reason, "identity": self.identity, "q": self.q,
            "search_scope": "address_and_names" if sees_contact_addresses(role) else "names_only",
        }


def read_recipients(cur: Any, campaign_id: str, query: RecipientQuery, role: str | None) -> dict[str, Any]:
    where, params = query.where(role)
    params["campaign"] = campaign_id
    base = f"""
      from ({RECIPIENT_BASE_SQL} where r.campaign_id = %(campaign)s) b
      left join crm.person pe on pe.id = b.person_id
      left join crm.organization org on org.id = b.organization_id
     where {where}
    """
    cur.execute(f"select count(*)::int {base}", params)  # noqa: S608 - fragments are constants
    total_rows = cur.fetchone()[0]
    params["limit"] = query.page_size
    params["offset"] = (query.page - 1) * query.page_size
    cur.execute(
        f"""
        select b.id::text as recipient_id, b.address_norm as address, b.state, b.exclusion_reasons,
               b.person_id::text as person_id, pe.display_name as person_name,
               b.organization_id::text as organization_id, org.name as organization_name,
               b.attempts, b.accepted, b.rejected, b.bounced_attempts, b.confirmed,
               b.first_accepted_at, b.last_accepted_at, b.error_class, b.bounce_class,
               b.provider_message_id, b.replies, b.last_reply_at
          {base}
         order by b.last_accepted_at nulls last, b.created_at, b.id
         limit %(limit)s offset %(offset)s
        """,  # noqa: S608 - fragments are constants
        params,
    )
    cols = [d[0] for d in cur.description]
    rows = []
    for raw in cur.fetchall():
        r = dict(zip(cols, raw, strict=True))
        rows.append({
            "recipient_id": r["recipient_id"],
            "address": shown_address(r["address"], role),
            "identity": "crm_person" if r["person_id"] else "historical_address",
            "person_id": r["person_id"],
            "person_name": r["person_name"],
            "organization_id": r["organization_id"],
            "organization_name": r["organization_name"] if r["organization_id"] else None,
            "state": r["state"],
            "outcome": outcome(r),
            "exclusion_reasons": [{"code": c, "label": EXCLUSION_LABEL.get(c, c)} for c in (r["exclusion_reasons"] or [])],
            "attempts": int(r["attempts"]),
            "accepted_attempts": int(r["accepted"]),
            "rejected_attempts": int(r["rejected"]),
            "delivery_confirmed": int(r["confirmed"]) > 0,
            "sent_at": _iso(r["last_accepted_at"]),
            "first_sent_at": _iso(r["first_accepted_at"]),
            "rejection": r["error_class"],
            "bounce": "bounced" if (r["state"] == "bounced" or r["bounced_attempts"]) else None,
            "bounce_class": r["bounce_class"],
            "gmail_url": gmail_url(r["provider_message_id"]),
            "replies": int(r["replies"]),
            "last_reply_at": _iso(r["last_reply_at"]),
        })
    if rows:
        cur.execute(_BAJA_BY_ADDRESS_SQL, {"campaign": campaign_id, "ids": [r["recipient_id"] for r in rows]})
        registered = {rid: state for rid, state in cur.fetchall()}
        for r in rows:
            r["baja"] = registered.get(r["recipient_id"])
    return {
        "rows": rows,
        "page": query.page,
        "page_size": query.page_size,
        "total_rows": total_rows,
        "pages": max(1, -(-total_rows // query.page_size)),
        "filters": query.as_dict(role),
    }


# --------------------------------------------------------------------------- replies

#: Per recipient: a «BAJA» registered for its exact address (a W10 unsubscribe block, or one a
#: «BAJA» was linked to) → ``registered``; a «BAJA» held for review → ``pending_review``. By
#: address, never by reply: W10 records a known address without lineage to any message.
_BAJA_BY_ADDRESS_SQL = """
select r.id::text,
       case when exists (
              select 1 from outbound.contact_control c
               where c.scope = 'address' and c.kind = 'block' and c.value_norm = r.address_norm
                 and (c.reason = 'unsubscribe' or exists (
                       select 1 from evidence.assertion a
                        where a.kind = 'unsubscribe_request' and a.resolved_kind = 'contact_control'
                          and a.resolved_id = c.id)))
            then 'registered'
            when exists (
              select 1 from evidence.assertion a
               where a.kind = 'unsubscribe_request' and a.resolution = 'unresolved' and a.value_norm = r.address_norm)
            then 'pending_review'
       end
  from outbound.campaign_recipient r
 where r.campaign_id = %(campaign)s and r.id = any(%(ids)s::uuid[])
"""


def read_replies(cur: Any, campaign_id: str, role: str | None) -> dict[str, Any]:
    """Only what is stored with lineage. No body is stored (``comms.message`` keeps none), so no
    excerpt is ever shown; a Gmail link only when the message's provider id is stored."""
    cur.execute(
        """
        select cr.id::text as reply_id, cr.received_at, cr.proposed_class, cr.proposed_by,
               cr.operator_class, cr.classified_at,
               r.id::text as recipient_id, r.address_norm as address,
               pe.display_name as person_name, org.name as organization_name,
               m.provider_message_id
          from outbound.campaign_reply cr
          join outbound.campaign_recipient r on r.id = cr.campaign_recipient_id
          left join crm.person pe on pe.id = r.person_id
          left join crm.organization org on org.id = r.organization_id
          left join comms.message m on m.id = cr.message_id
         where cr.campaign_id = %(campaign)s
         order by cr.received_at desc, cr.id
        """,
        {"campaign": campaign_id},
    )
    cols = [d[0] for d in cur.description]
    items: list[dict[str, Any]] = []
    for raw in cur.fetchall():
        r = dict(zip(cols, raw, strict=True))
        cls = r["operator_class"] or r["proposed_class"]
        items.append({
            "id": r["reply_id"],
            "kind": "baja" if cls == "unsubscribe_request" else "reply",
            "class": cls,
            "class_label": REPLY_CLASS_LABEL.get(cls, cls),
            "classified_by": "operator" if r["operator_class"] else r["proposed_by"],
            "received_at": _iso(r["received_at"]),
            "association": "recipient",
            "recipient_id": r["recipient_id"],
            "address": shown_address(r["address"], role),
            "person_name": r["person_name"],
            "organization_name": r["organization_name"],
            "excerpt": None,
            "gmail_url": gmail_url(r["provider_message_id"]),
        })
    # «BAJA» recorded through W10 whose In-Reply-To proved a message this campaign sent.
    cur.execute(
        """
        select a.id::text as assertion_id, a.value_norm as address, a.resolution,
               (a.value->>'observed_at')::timestamptz as observed_at, a.created_at,
               r.id::text as recipient_id, pe.display_name as person_name, org.name as organization_name
          from evidence.assertion a
          join comms.message m on m.id = case when a.value ? 'lineage_message_id'
                                              then (a.value->>'lineage_message_id')::uuid end
          join outbound.send_attempt s on s.id = m.send_attempt_id
          left join outbound.campaign_recipient r on r.id = s.campaign_recipient_id
          left join crm.person pe on pe.id = r.person_id
          left join crm.organization org on org.id = r.organization_id
         where a.kind = 'unsubscribe_request' and s.campaign_id = %(campaign)s
         order by a.created_at desc, a.id
        """,
        {"campaign": campaign_id},
    )
    cols = [d[0] for d in cur.description]
    lineage = [dict(zip(cols, raw, strict=True)) for raw in cur.fetchall()]
    for r in lineage:
        items.append({
            "id": r["assertion_id"], "kind": "baja", "class": "unsubscribe_request",
            "class_label": REPLY_CLASS_LABEL["unsubscribe_request"], "classified_by": "baja_grammar",
            "received_at": _iso(r["observed_at"] or r["created_at"]), "association": "send_lineage",
            "recipient_id": r["recipient_id"], "address": shown_address(r["address"], role), "person_name": r["person_name"],
            "organization_name": r["organization_name"], "excerpt": None, "gmail_url": None,
            "resolution": r["resolution"],
        })
    # «BAJA» held for review whose exact address is in this campaign's audience. Associated by
    # address only — never by a reply — and labelled so.
    cur.execute(
        """
        select a.id::text as assertion_id, a.value_norm as address, a.value->>'review_reason' as review_reason,
               (a.value->>'observed_at')::timestamptz as observed_at, a.created_at,
               r.id::text as recipient_id, pe.display_name as person_name, org.name as organization_name
          from evidence.assertion a
          join outbound.campaign_recipient r on r.campaign_id = %(campaign)s and r.address_norm = a.value_norm
          left join crm.person pe on pe.id = r.person_id
          left join crm.organization org on org.id = r.organization_id
         where a.kind = 'unsubscribe_request' and a.resolution = 'unresolved'
         order by a.created_at desc, a.id
        """,
        {"campaign": campaign_id},
    )
    cols = [d[0] for d in cur.description]
    for raw in cur.fetchall():
        r = dict(zip(cols, raw, strict=True))
        items.append({
            "id": r["assertion_id"], "kind": "baja_pending_review", "class": "unsubscribe_request",
            "class_label": "BAJA en revisión", "classified_by": "baja_grammar",
            "received_at": _iso(r["observed_at"] or r["created_at"]), "association": "address_match",
            "recipient_id": r["recipient_id"], "address": shown_address(r["address"], role), "person_name": r["person_name"],
            "organization_name": r["organization_name"], "excerpt": None, "gmail_url": None,
            "review_reason": r["review_reason"],
        })
    # Recipients of this campaign whose exact address has a registered «BAJA»: associated by
    # address only (which message it answered is not recorded), so counted, never listed as replies.
    cur.execute(
        """
        select count(*)::int from outbound.campaign_recipient r
         where r.campaign_id = %(campaign)s
           and exists (select 1 from outbound.contact_control c
                        where c.scope = 'address' and c.kind = 'block' and c.value_norm = r.address_norm
                          and (c.reason = 'unsubscribe' or exists (
                                select 1 from evidence.assertion a
                                 where a.kind = 'unsubscribe_request' and a.resolved_kind = 'contact_control'
                                   and a.resolved_id = c.id)))
        """,
        {"campaign": campaign_id},
    )
    by_address = cur.fetchone()[0]
    # What exists only in aggregate: «BAJA» requests no sent message can be tied to.
    cur.execute(
        """
        select count(*)::int from evidence.assertion a
         where a.kind = 'unsubscribe_request' and not (a.value ? 'lineage_message_id')
        """
    )
    unassociated = cur.fetchone()[0]
    stored_replies = sum(1 for i in items if i["association"] == "recipient")
    counts = {k: sum(1 for i in items if i["kind"] == k) for k in ("reply", "baja", "baja_pending_review")}
    return {
        "items": items,
        "counts": counts,
        "sync": replies_state(stored_replies, len(lineage)),
        "baja_by_address": {
            "recipients": by_address,
            "label": "Destinatarios de esta campaña con una BAJA registrada para su dirección. Asociada por "
                     "dirección, no por respuesta: no se sabe a qué correo respondieron.",
        },
        "unassociated": {
            "baja_without_campaign_lineage": unassociated,
            "label": "BAJA registradas sin vínculo con un envío de campaña (agregado de todas las campañas)",
        },
        "excerpts": "Los mensajes no guardan cuerpo en el CRM: no se muestra ningún extracto.",
        "gmail_called": False,
    }


# --------------------------------------------------------------------------- audit


def immutability_enforced(cur: Any) -> bool:
    """Whether this database carries the archived-campaign guard (migration 20260928090000).

    The dashboard says "the database refuses edits" only when it does: a clean room not yet
    migrated answers the same reads without the guard.
    """
    cur.execute(
        "select count(*) = 3 from pg_trigger where not tgisinternal and tgname in "
        "('campaign_archived_immutable', 'campaign_recipient_archived_immutable', 'send_attempt_archived_immutable')"
    )
    return bool(cur.fetchone()[0])


def _payload_summary(payload: Any) -> dict[str, Any]:
    """Scalar payload fields, long text shortened to its length: an audit line, not a dump."""
    if not isinstance(payload, Mapping):
        return {}
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, str) and len(value) > 120:
            out[key] = f"({len(value)} caracteres)"
        elif isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
        elif isinstance(value, list):
            out[key] = f"({len(value)} elementos)"
        else:
            out[key] = "(objeto)"
    return out


def read_audit(cur: Any, campaign_id: str) -> dict[str, Any] | None:
    cur.execute(
        """
        select c.id::text as campaign_id, c.name, c.status, c.version, c.created_at, c.updated_at,
               c.approved_at, c.content_frozen_at, c.audience_frozen_at,
               sr.id::text as source_record_id, sr.kind as source_kind, sr.dedupe_key,
               sr.payload->>'name' as manifest_name, sr.payload_sha256, sr.acquired_at,
               sr.review_status as source_review_status
          from outbound.campaign c
          left join evidence.source_record sr on sr.id = c.origin_source_record_id
         where c.id = %(campaign)s
        """,
        {"campaign": campaign_id},
    )
    cols = [d[0] for d in cur.description]
    row = cur.fetchone()
    if row is None:
        return None
    c = dict(zip(cols, row, strict=True))
    cur.execute(
        """
        select e.seq, e.event_type, e.recorded_at, e.actor_kind, o.display_name as actor_name, e.payload
          from crm.domain_event e
          left join platform.operator o on o.id = e.actor_operator_id
         where e.aggregate_kind = 'campaign' and e.aggregate_id = %(campaign)s
         order by e.seq
        """,
        {"campaign": campaign_id},
    )
    events = [
        {"seq": seq, "event_type": et, "recorded_at": _iso(at), "actor_kind": ak, "actor_name": an,
         "payload": _payload_summary(p)}
        for seq, et, at, ak, an, p in cur.fetchall()
    ]
    cur.execute(
        """
        select e.event_type, count(*)::int
          from crm.domain_event e
          join outbound.send_attempt s on s.id = e.aggregate_id
         where e.aggregate_kind = 'send_attempt' and s.campaign_id = %(campaign)s
         group by 1 order by 1
        """,
        {"campaign": campaign_id},
    )
    attempt_events = [{"event_type": t, "count": n} for t, n in cur.fetchall()]
    cur.execute("select current_database()")
    database = cur.fetchone()[0]
    enforced = immutability_enforced(cur)
    archived = c["status"] == "archived"
    imported = c["source_record_id"] is not None
    return {
        "campaign_id": c["campaign_id"],
        "name": c["name"],
        "status": c["status"],
        "version": c["version"],
        "row": {
            "created_at": _iso(c["created_at"]), "updated_at": _iso(c["updated_at"]),
            "approved_at": _iso(c["approved_at"]), "content_frozen_at": _iso(c["content_frozen_at"]),
            "audience_frozen_at": _iso(c["audience_frozen_at"]),
        },
        "origin": {
            "kind": "imported_v1" if imported else "native_v2",
            "source_record_id": c["source_record_id"],
            "source_kind": c["source_kind"],
            "dedupe_key": c["dedupe_key"],
            "manifest_name": c["manifest_name"],
            "payload_sha256": c["payload_sha256"],
            "acquired_at": _iso(c["acquired_at"]),
            "review_status": c["source_review_status"],
        },
        "events": events,
        "attempt_events": attempt_events,
        "events_note": (
            "El importador histórico no registra eventos de dominio: la procedencia de esta campaña es el "
            "manifiesto de migración y la fecha de carga de la fila."
            if imported and not events else None
        ),
        "immutable": archived,
        "immutable_enforced_by_database": enforced,
        "immutable_note": (
            None if not archived else
            "Campaña archivada: la base de datos rechaza editarla, reabrirla, ampliarla o borrarla, y cambiar "
            "lo registrado de sus destinatarios o intentos de envío. Sólo puede vincularse un destinatario a su "
            "persona o institución del CRM." if enforced else
            "Campaña archivada: el CRM no ofrece ninguna acción sobre ella, pero esta base de datos aún no tiene "
            "la protección que rechaza editarla (migración 20260928090000 sin aplicar)."
        ),
        "actions": [] if archived else None,
        "storage": {"tables": ["outbound.campaign", "crm.domain_event", "evidence.source_record"], "database": database},
    }
