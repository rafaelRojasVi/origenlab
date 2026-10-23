"""Is this prospect new? Decided at read time, never stored (spec 2026-10-23 §6).

Order: blocked (a `block` contact control on the address or its domain) → known (a CRM contact
point, mail we exchanged, or a `prior_contact` control) → new. A known institution does not make a
person known; it is a reason shown on the card. Free-mail domains never identify an organization."""

from __future__ import annotations

from typing import Any, Literal, TypedDict
from urllib.parse import urlparse

from origenlab_api.v2.commands import normalize_email, normalize_organization_name

FREE_MAIL_DOMAINS: frozenset[str] = frozenset({
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.cl", "outlook.com", "outlook.cl", "live.com",
    "live.cl", "yahoo.com", "yahoo.es", "icloud.com", "me.com", "proton.me", "protonmail.com",
})

State = Literal["blocked", "known", "new"]


class Classification(TypedDict):
    state: State
    reasons: list[str]
    organization_candidates: list[dict[str, str]]


_BLOCK = """
select scope, reason from outbound.contact_control
 where kind = 'block' and (until_at is null or until_at > now())
   and ((scope = 'address' and value_norm = %(email)s) or (scope = 'domain' and value_norm = %(domain)s))
 limit 1
"""
_KNOWN = """
select exists (select 1 from crm.contact_point where kind = 'email' and value_norm = %(email)s) as in_crm,
       (select count(*) from comms.message_participant where address_norm = %(email)s)::int as mails,
       exists (select 1 from outbound.contact_control
                where scope = 'address' and value_norm = %(email)s and kind = 'prior_contact') as prior
"""
_CANDIDATES = """
select o.id::text as id, o.name from crm.organization o
 where o.merged_into_organization_id is null
   and (o.id in (select d.organization_id from crm.organization_domain d where d.domain_norm = %(domain)s)
        or lower(regexp_replace(btrim(o.name), '\\s+', ' ', 'g')) = %(name)s)
 order by o.name, o.id
"""


def domain_of(email: str | None, website: str | None) -> str | None:
    if email and "@" in email:
        return email.rsplit("@", 1)[1]
    host = urlparse(website).hostname if website else None
    if host and host.startswith("www."):
        host = host[4:]
    return host.lower() if host else None


def classify(cur: Any, *, email: str | None, organization_name: str, website: str | None) -> Classification:
    email = normalize_email(email) if email else None
    domain = domain_of(email, website)
    org_domain = None if domain in FREE_MAIL_DOMAINS else domain
    params = {"email": email, "domain": domain, "name": normalize_organization_name(organization_name)}
    reasons: list[str] = []
    cur.execute(_BLOCK, params)
    row = cur.fetchone()
    if row is not None:
        return {"state": "blocked", "reasons": ["En Bajas: " + str(row[1])], "organization_candidates": []}
    state: State = "new"
    if email:
        cur.execute(_KNOWN, params)
        in_crm, mails, prior = cur.fetchone()
        if in_crm:
            reasons.append("El correo ya está en el CRM")
        if mails:
            reasons.append(f"Ya nos escribimos ({mails} {'correo' if mails == 1 else 'correos'})")
        if prior:
            reasons.append("Ya fue contactado en una campaña")
        if in_crm or mails or prior:
            state = "known"
    cur.execute(_CANDIDATES, {**params, "domain": org_domain})
    candidates = [{"id": r[0], "name": r[1]} for r in cur.fetchall()]
    if len(candidates) == 1:
        reasons.append("Institución conocida: " + candidates[0]["name"])
    elif len(candidates) > 1:
        reasons.append("Posible duplicado de institución")
    if state == "new" and not candidates:
        reasons.insert(0, "No está en el CRM ni en nuestros correos")
    return {"state": state, "reasons": reasons, "organization_candidates": candidates}
