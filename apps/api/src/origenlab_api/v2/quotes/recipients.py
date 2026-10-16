"""Who a quote may go to: the one address rule the workspace's candidates and the save's pick share.

OrigenLab's own domains come from `mail_rules.OWN_DOMAINS`, the single list the email rules use;
Labdelivery's from `catalog.keys.is_labdelivery` (any address or name that names it) and its
domain. A subdomain is its domain's (`correo.origenlab.cl` is OrigenLab).
"""
from __future__ import annotations

import re

from origenlab_api.v2.catalog.keys import is_labdelivery
from origenlab_api.v2.mail_rules import OWN_DOMAINS

LABDELIVERY_DOMAINS: frozenset[str] = frozenset({"labdelivery.cl"})
#: Every domain (and its subdomains) no quote goes to.
UNOFFERABLE_DOMAINS: frozenset[str] = OWN_DOMAINS | LABDELIVERY_DOMAINS
_DOMAIN = re.compile(r"^[a-z0-9.-]+$")


def unofferable_address_sql(column: str) -> str:
    """SQL true for an address `not_a_recipient` refuses, as far as SQL can tell: OrigenLab's own or
    Labdelivery's domain, a subdomain of one, or an address naming Labdelivery. Built from the
    module's constant domains (checked to be plain domain text); `%%` because every statement that
    uses it carries parameters. It orders, never decides: the rule itself is applied in Python."""
    domains = sorted(UNOFFERABLE_DOMAINS)
    assert all(_DOMAIN.match(d) for d in domains)  # noqa: S101 - constants, never input
    exact = ", ".join(f"'{d}'" for d in domains)
    under = ", ".join(f"'%%.{d}'" for d in domains)
    domain = f"split_part({column}, '@', 2)"
    return (f"({domain} in ({exact}) or {domain} like any (array[{under}]) "
            f"or regexp_replace({column}, '[^a-z0-9]', '', 'g') like '%%labdelivery%%')")


#: Which of a person's addresses stands for them — the one a quote prints, the workspace shows, the
#: new-quote dialog offers and `create-quote` takes the institution from: an active one, then one
#: a quote may go to, then the oldest. (`c` is `crm.contact_point`.)
PERSON_ADDRESS_ORDER = f"(c.status = 'active') desc, {unofferable_address_sql('c.value_norm')} asc, c.created_at, c.id"


def _under(domain: str, domains: frozenset[str]) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in domains)


def not_a_recipient(email: str | None, name: str | None = None) -> str | None:
    """Why this address (and the name it goes by) is no quote recipient — `labdelivery_refused` or
    `recipient_own_address` — or None when it may be one. A missing address is not refused here."""
    domain = (email or "").rpartition("@")[2].strip().lower()
    if is_labdelivery(email) or is_labdelivery(name) or (domain and _under(domain, LABDELIVERY_DOMAINS)):
        return "labdelivery_refused"
    if domain and _under(domain, OWN_DOMAINS):
        return "recipient_own_address"
    return None
