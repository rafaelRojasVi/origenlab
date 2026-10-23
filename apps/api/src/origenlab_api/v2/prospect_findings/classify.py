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
#: Live institutions holding a domain: the domain row not removed, the institution neither merged nor archived.
_BY_DOMAIN = """
select o.id::text as id, o.name from crm.organization o
  join crm.organization_domain d on d.organization_id = o.id
 where d.domain_norm = %(domain)s and d.removed_at is null
   and o.merged_into_organization_id is null and o.status <> 'archived'
 order by o.name, o.id
"""
_BY_NAME = """
select o.id::text as id, o.name from crm.organization o
 where o.merged_into_organization_id is null and o.status <> 'archived'
   and lower(regexp_replace(btrim(o.name), '\\s+', ' ', 'g')) = %(name)s
 order by o.name, o.id
"""
#: Second-level public suffixes: walking up a host never stops on one of these (`sag.gob.cl` ≠ `gob.cl`).
_PUBLIC_SECOND_LEVEL: frozenset[str] = frozenset({"gob.cl", "mil.cl", "co.cl", "com.ar", "com.br", "co.uk", "ac.uk"})


def domain_of(email: str | None, website: str | None) -> str | None:
    """The email's domain, else the website's host without `www.`."""
    domains = _domains(email, website, keep_free_mail=True)
    return domains[0] if domains else None


def _host(website: str | None) -> str | None:
    host = urlparse(website).hostname if website else None
    if host and host.startswith("www."):
        host = host[4:]
    return host.lower() if host else None


def _domains(email: str | None, website: str | None, *, keep_free_mail: bool = False) -> list[str]:
    found = [email.rsplit("@", 1)[1].lower()] if email and "@" in email else []
    host = _host(website)
    if host:
        found.append(host)
    out: list[str] = []
    for d in found:
        if d not in out and (keep_free_mail or d not in FREE_MAIL_DOMAINS):
            out.append(d)
    return out


def institution_domains(email: str | None, website: str | None) -> list[str]:
    """The domains that may name the institution: the email's (not free mail), then the website's."""
    return _domains(email, website)


#: Site-hosting platforms: `lab.wixsite.com` is the lab, never the platform behind it.
_HOSTING_PLATFORMS: frozenset[str] = frozenset({
    "wixsite.com", "sites.google.com", "google.com", "blogspot.com", "wordpress.com", "github.io",
    "weebly.com", "squarespace.com", "webnode.cl", "jimdofree.com",
})


def _walk_up(domain: str) -> list[str]:
    """`quimica.uni.cl` → [`quimica.uni.cl`, `uni.cl`]: a faculty subdomain reaches its university.
    It never climbs onto a public suffix or a site-hosting platform."""
    if any(domain == p or domain.endswith("." + p) for p in _HOSTING_PLATFORMS):
        return [domain]
    labels = domain.split(".")
    out = []
    for i in range(len(labels) - 1):
        candidate = ".".join(labels[i:])
        if candidate in _PUBLIC_SECOND_LEVEL:
            break
        out.append(candidate)
    return out


def organization_candidates(cur: Any, *, email: str | None, organization_name: str,
                            website: str | None) -> list[dict[str, str]]:
    """The most specific domain that names a live institution wins (the email's before the website's,
    a subdomain before its parent); institutions with the same normalised name are added."""
    found: list[dict[str, str]] = []
    for base in institution_domains(email, website):
        for domain in _walk_up(base):
            cur.execute(_BY_DOMAIN, {"domain": domain})
            found = [{"id": r[0], "name": r[1]} for r in cur.fetchall()]
            if found:
                break
        if found:
            break
    cur.execute(_BY_NAME, {"name": normalize_organization_name(organization_name)})
    for r in cur.fetchall():
        if all(c["id"] != r[0] for c in found):
            found.append({"id": r[0], "name": r[1]})
    return found


def classify(cur: Any, *, email: str | None, organization_name: str, website: str | None) -> Classification:
    email = normalize_email(email) if email else None
    domain = domain_of(email, None)
    params = {"email": email, "domain": domain}
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
    candidates = organization_candidates(cur, email=email, organization_name=organization_name, website=website)
    if len(candidates) == 1:
        reasons.append("Institución conocida: " + candidates[0]["name"])
    elif len(candidates) > 1:
        reasons.append("Posible duplicado de institución")
    if state == "new" and not candidates:
        reasons.insert(0, "No está en el CRM ni en nuestros correos")
    return {"state": state, "reasons": reasons, "organization_candidates": candidates}
