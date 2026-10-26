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


_MANY_BLOCKS = """
select scope, value_norm, reason from outbound.contact_control
 where kind = 'block' and (until_at is null or until_at > now())
   and ((scope = 'address' and value_norm = any(%(emails)s)) or (scope = 'domain' and value_norm = any(%(domains)s)))
"""
_MANY_CRM = "select value_norm from crm.contact_point where kind = 'email' and value_norm = any(%(emails)s)"
_MANY_MAILS = """
select address_norm, count(*)::int from comms.message_participant
 where address_norm = any(%(emails)s) group by address_norm
"""
_MANY_PRIOR = """
select value_norm from outbound.contact_control
 where scope = 'address' and kind = 'prior_contact' and value_norm = any(%(emails)s)
"""
_MANY_BY_DOMAIN = """
select d.domain_norm, o.id::text, o.name from crm.organization o
  join crm.organization_domain d on d.organization_id = o.id
 where d.domain_norm = any(%(domains)s) and d.removed_at is null
   and o.merged_into_organization_id is null and o.status <> 'archived'
 order by o.name, o.id
"""
_MANY_BY_NAME = """
select lower(regexp_replace(btrim(o.name), '\\s+', ' ', 'g')) as norm, o.id::text, o.name from crm.organization o
 where o.merged_into_organization_id is null and o.status <> 'archived'
   and lower(regexp_replace(btrim(o.name), '\\s+', ' ', 'g')) = any(%(names)s)
 order by o.name, o.id
"""


class Subject(TypedDict):
    email: str | None
    organization_name: str
    website: str | None
    block_only: bool


def classify_many(cur: Any, subjects: list[Subject]) -> list[Classification]:
    """Classify every subject with a fixed number of statements (six at most), whatever their count.

    Same rules and order as one at a time: blocked → known → new; the most specific live domain
    names the institution (the email's before the website's, a subdomain before its parent), then
    institutions with the same normalised name."""
    emails = [normalize_email(s["email"]) if s["email"] else None for s in subjects]
    own_domain = [domain_of(e, None) for e in emails]
    ladders = [[level for base in institution_domains(e, s["website"]) for level in _walk_up(base)]
               for e, s in zip(emails, subjects, strict=True)]
    names = [normalize_organization_name(s["organization_name"]) for s in subjects]
    all_emails = sorted({e for e in emails if e})
    all_domains = sorted({d for d in own_domain if d})
    cur.execute(_MANY_BLOCKS, {"emails": all_emails, "domains": all_domains})
    blocks: dict[tuple[str, str], str] = {(r[0], r[1]): r[2] for r in cur.fetchall()}
    open_ = [i for i, s in enumerate(subjects)
             if not s["block_only"] and ("address", emails[i]) not in blocks and ("domain", own_domain[i]) not in blocks]
    in_crm: set[str] = set()
    mails: dict[str, int] = {}
    prior: set[str] = set()
    by_domain: dict[str, list[dict[str, str]]] = {}
    by_name: dict[str, list[dict[str, str]]] = {}
    open_emails = sorted({emails[i] for i in open_ if emails[i]})
    if open_emails:
        cur.execute(_MANY_CRM, {"emails": open_emails})
        in_crm = {r[0] for r in cur.fetchall()}
        cur.execute(_MANY_MAILS, {"emails": open_emails})
        mails = {r[0]: r[1] for r in cur.fetchall()}
        cur.execute(_MANY_PRIOR, {"emails": open_emails})
        prior = {r[0] for r in cur.fetchall()}
    open_domains = sorted({d for i in open_ for d in ladders[i]})
    if open_domains:
        cur.execute(_MANY_BY_DOMAIN, {"domains": open_domains})
        for d, oid, name in cur.fetchall():
            by_domain.setdefault(d, []).append({"id": oid, "name": name})
    open_names = sorted({names[i] for i in open_})
    if open_names:
        cur.execute(_MANY_BY_NAME, {"names": open_names})
        for norm, oid, name in cur.fetchall():
            by_name.setdefault(norm, []).append({"id": oid, "name": name})

    out: list[Classification] = []
    for i, s in enumerate(subjects):
        e, d = emails[i], own_domain[i]
        reason = blocks.get(("address", e)) if e else None
        if reason is None and d:
            reason = blocks.get(("domain", d))
        if reason is not None:
            out.append({"state": "blocked", "reasons": ["En Bajas: " + str(reason)], "organization_candidates": []})
            continue
        if s["block_only"]:
            out.append({"state": "new", "reasons": [], "organization_candidates": []})
            continue
        reasons: list[str] = []
        state: State = "new"
        if e:
            n = mails.get(e, 0)
            if e in in_crm:
                reasons.append("El correo ya está en el CRM")
            if n:
                reasons.append(f"Ya nos escribimos ({n} {'correo' if n == 1 else 'correos'})")
            if e in prior:
                reasons.append("Ya fue contactado en una campaña")
            if e in in_crm or n or e in prior:
                state = "known"
        candidates = next((list(by_domain[level]) for level in ladders[i] if by_domain.get(level)), [])
        for c in by_name.get(names[i], []):
            if all(x["id"] != c["id"] for x in candidates):
                candidates.append(c)
        if len(candidates) == 1:
            reasons.append("Institución conocida: " + candidates[0]["name"])
        elif len(candidates) > 1:
            reasons.append("Dos instituciones posibles: " + " / ".join(c["name"] for c in candidates))
        if state == "new" and not candidates:
            reasons.insert(0, "No está en el CRM ni en nuestros correos")
        out.append({"state": state, "reasons": reasons, "organization_candidates": candidates})
    return out


def classify(cur: Any, *, email: str | None, organization_name: str, website: str | None,
             block_only: bool = False) -> Classification:
    """One finding (the `contact-prospect` re-check under its lock): `classify_many` of one."""
    return classify_many(cur, [{"email": email, "organization_name": organization_name, "website": website,
                                "block_only": block_only}])[0]
