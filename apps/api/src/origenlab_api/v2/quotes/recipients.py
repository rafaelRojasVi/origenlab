"""Who a quote may go to: the one address rule the workspace's candidates and the save's pick share.

OrigenLab's own domains come from `mail_rules.OWN_DOMAINS`, the single list the email rules use;
Labdelivery's from `catalog.keys.is_labdelivery` (any address or name that names it) and its
domain. A subdomain is its domain's (`correo.origenlab.cl` is OrigenLab).
"""
from __future__ import annotations

from origenlab_api.v2.catalog.keys import is_labdelivery
from origenlab_api.v2.mail_rules import OWN_DOMAINS

LABDELIVERY_DOMAINS: frozenset[str] = frozenset({"labdelivery.cl"})
#: Every domain (and its subdomains) no quote goes to.
UNOFFERABLE_DOMAINS: frozenset[str] = OWN_DOMAINS | LABDELIVERY_DOMAINS


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
