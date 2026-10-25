"""«¿Ya lo tenemos?» — where a correo, a name or a domain already is in OrigenLab (owner 2026-10-25:
«a searcher to see if the email is already in the system anywhere»).

One query string, three readings:

* **an email** (`ana@lab.cl`) — exactly that address: the CRM person or institution that owns it, how
  many emails we exchanged with it (`comms.message_participant`), whether it or its domain is in Bajas
  (`outbound.contact_control` block), whether a campaign already reached it (`prior_contact`), the
  institutions of its domain, and the prospects that carry it;
* **a domain** (`lab.cl`) — the institutions with that domain, the CRM addresses under it, the emails
  exchanged with it and a domain block;
* **a name** (three or more characters) — CRM people and institutions, and prospects, whose name
  contains it (accent- and case-insensitive).

Read-only, a fixed number of statements, at most `LIMIT` rows per group. Free-mail domains
(`gmail.com`, …) never name an institution. Contact addresses are masked for a role that does not
read them by the route (`ContactRedactingRoute`), like every `/v2` read.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from origenlab_api.v2.prospect_findings.classify import FREE_MAIL_DOMAINS

LIMIT = 8
_EMAIL = re.compile(r"^[^@\s]+@([a-z0-9-]+(?:\.[a-z0-9-]+)+)$")
_DOMAIN = re.compile(r"^[a-z0-9-]+(?:\.[a-z0-9-]+)+$")

_PEOPLE_BY_EMAIL = """
select p.id::text as person_id, p.display_name, cp.value_norm as email,
       (select o.name from crm.affiliation a join crm.organization o on o.id = a.organization_id
         where a.person_id = p.id order by a.created_at desc limit 1) as organization_name
  from crm.contact_point cp join crm.person p on p.id = cp.person_id
 where cp.kind = 'email' and cp.value_norm = any(%(emails)s) and p.merged_into_person_id is null
 order by p.display_name limit %(limit)s
"""
_ORG_MAILBOXES = """
select o.id::text as organization_id, o.name, cp.value_norm as email
  from crm.contact_point cp join crm.organization o on o.id = cp.organization_id
 where cp.kind = 'email' and cp.person_id is null and cp.value_norm = any(%(emails)s)
   and o.merged_into_organization_id is null
 order by o.name limit %(limit)s
"""
_ADDRESSES_IN_DOMAIN = """
select cp.value_norm as email, p.id::text as person_id, p.display_name,
       o.id::text as organization_id, o.name as organization_name
  from crm.contact_point cp
  left join crm.person p on p.id = cp.person_id and p.merged_into_person_id is null
  left join crm.organization o on o.id = cp.organization_id and o.merged_into_organization_id is null
 where cp.kind = 'email' and split_part(cp.value_norm, '@', 2) = %(domain)s
 order by cp.value_norm limit %(limit)s
"""
_MAIL_BY_ADDRESS = """
select count(distinct mp.message_id)::int as messages, max(m.internal_date) as last_at
  from comms.message_participant mp join comms.message m on m.id = mp.message_id
 where mp.address_norm = %(email)s
"""
_MAIL_BY_DOMAIN = """
select count(distinct mp.message_id)::int as messages, max(m.internal_date) as last_at,
       count(distinct mp.address_norm)::int as addresses
  from comms.message_participant mp join comms.message m on m.id = mp.message_id
 where split_part(mp.address_norm, '@', 2) = %(domain)s
"""
_CONTROLS = """
select scope, value_norm, kind, reason from outbound.contact_control
 where kind in ('block', 'prior_contact') and (until_at is null or until_at > now())
   and ((scope = 'address' and value_norm = any(%(emails)s)) or (scope = 'domain' and value_norm = any(%(domains)s)))
 order by kind, scope
"""
_ORGS_BY_DOMAIN = """
select distinct o.id::text as organization_id, o.name, d.domain_norm as domain
  from crm.organization o join crm.organization_domain d on d.organization_id = o.id
 where d.domain_norm = any(%(domains)s) and d.removed_at is null
   and o.merged_into_organization_id is null and o.status <> 'archived'
 order by o.name limit %(limit)s
"""
# The prospects carrying the address, by the finding's own email or a person's correction of it.
_PROSPECTS_BY_EMAIL = """
select s.id::text as finding_id, s.payload->>'person_name' as person_name,
       s.payload->>'organization_name' as organization_name
  from evidence.source_record s
 where s.kind = 'prospect_finding' and not s.is_quarantined and s.superseded_by_source_record_id is null
   and (lower(s.payload->>'email') = any(%(emails)s)
        or exists (select 1 from crm.prospect_edit e where e.source_record_id = s.id
                     and lower(e.changes->>'email') = any(%(emails)s)))
 order by s.acquired_at desc limit %(limit)s
"""
_PEOPLE_BY_NAME = """
select p.id::text as person_id, p.display_name,
       (select o.name from crm.affiliation a join crm.organization o on o.id = a.organization_id
         where a.person_id = p.id order by a.created_at desc limit 1) as organization_name,
       (select cp.value_norm from crm.contact_point cp where cp.person_id = p.id and cp.kind = 'email'
         order by cp.created_at limit 1) as email
  from crm.person p
 where p.merged_into_person_id is null and p.status <> 'archived'
   and translate(lower(p.display_name), 'áéíóúüñ', 'aeiouun') like %(like)s
 order by p.display_name limit %(limit)s
"""
_ORGS_BY_NAME = """
select o.id::text as organization_id, o.name
  from crm.organization o
 where o.merged_into_organization_id is null and o.status <> 'archived'
   and translate(lower(o.name), 'áéíóúüñ', 'aeiouun') like %(like)s
 order by o.name limit %(limit)s
"""
_PROSPECTS_BY_NAME = """
select s.id::text as finding_id, s.payload->>'person_name' as person_name,
       s.payload->>'organization_name' as organization_name
  from evidence.source_record s
 where s.kind = 'prospect_finding' and not s.is_quarantined and s.superseded_by_source_record_id is null
   and translate(lower(coalesce(s.payload->>'person_name', '') || ' ' || coalesce(s.payload->>'organization_name', '')),
                 'áéíóúüñ', 'aeiouun') like %(like)s
 order by s.acquired_at desc limit %(limit)s
"""


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def _walk_up(domain: str) -> list[str]:
    labels = domain.split(".")
    return [".".join(labels[i:]) for i in range(len(labels) - 1)
            if ".".join(labels[i:]) not in {"gob.cl", "co.cl", "com.ar", "co.uk"}]


def _orgs_by_domain(cur: Any, domain: str) -> list[dict[str, Any]]:
    """The institutions of the most specific level of the domain that has any (`quimica.uni.cl`
    before `uni.cl`), as the prospect classifier decides; a free-mail domain names none."""
    if domain in FREE_MAIL_DOMAINS:
        return []
    levels = _walk_up(domain)
    rows = _rows(cur, _ORGS_BY_DOMAIN, {"domains": levels, "limit": LIMIT * 4})
    for level in levels:
        found = [r for r in rows if r["domain"] == level]
        if found:
            return found[:LIMIT]
    return []


def _rows(cur: Any, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    cur.execute(sql, params)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


def _mail(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row or not row.get("messages"):
        return None
    last = row.get("last_at")
    return {"messages": row["messages"], "last_at": last.isoformat() if hasattr(last, "isoformat") else last,
            **({"addresses": row["addresses"]} if "addresses" in row else {})}


def kind_of(q: str) -> str | None:
    """`email`, `domain`, `name`, or None for a query too short to search."""
    q = q.strip().lower()
    if _EMAIL.match(q):
        return "email"
    if _DOMAIN.match(q.removeprefix("@")):
        return "domain"
    return "name" if len(q) >= 3 else None


def lookup(cur: Any, q: str) -> dict[str, Any]:
    """Every place the query already is. Groups are empty lists (or null) when nothing matched."""
    q = q.strip().lower()
    kind = kind_of(q)
    out: dict[str, Any] = {"query": q, "kind": kind, "people": [], "organizations": [], "addresses": [],
                           "mail": None, "controls": [], "prospects": [], "found": False}
    if kind is None:
        return out
    lim = {"limit": LIMIT}
    if kind == "email":
        domain = q.rsplit("@", 1)[1]
        emails = {"emails": [q], **lim}
        out["people"] = _rows(cur, _PEOPLE_BY_EMAIL, emails)
        out["addresses"] = [{"email": r["email"], "person_id": None, "display_name": None,
                             "organization_id": r["organization_id"], "organization_name": r["name"]}
                            for r in _rows(cur, _ORG_MAILBOXES, emails)]
        out["mail"] = _mail(_rows(cur, _MAIL_BY_ADDRESS, {"email": q})[0])
        out["controls"] = _rows(cur, _CONTROLS, {"emails": [q], "domains": [domain]})
        out["organizations"] = _orgs_by_domain(cur, domain)
        out["prospects"] = _rows(cur, _PROSPECTS_BY_EMAIL, emails)
    elif kind == "domain":
        domain = q.removeprefix("@")
        out["query"] = domain
        out["organizations"] = _orgs_by_domain(cur, domain)
        out["addresses"] = _rows(cur, _ADDRESSES_IN_DOMAIN, {"domain": domain, **lim})
        out["mail"] = _mail(_rows(cur, _MAIL_BY_DOMAIN, {"domain": domain})[0])
        out["controls"] = _rows(cur, _CONTROLS, {"emails": [], "domains": [domain]})
    else:
        like = {"like": f"%{_fold(q)}%", **lim}
        out["people"] = _rows(cur, _PEOPLE_BY_NAME, like)
        out["organizations"] = _rows(cur, _ORGS_BY_NAME, like)
        out["prospects"] = _rows(cur, _PROSPECTS_BY_NAME, like)
    out["found"] = any(out[k] for k in ("people", "organizations", "addresses", "mail", "controls", "prospects"))
    return out
