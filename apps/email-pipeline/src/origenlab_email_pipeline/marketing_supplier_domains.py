"""Domains to exclude from cold outreach: known suppliers (proveedores).

Primary source: ``supplier_master.domain_norm`` (DeepSearch / workbook imports).
``contact_master`` does **not** encode buyer vs supplier — it aggregates everyone
seen in mail. Use this module in marketing exports, not heuristics on org type alone.
"""

from __future__ import annotations

import sqlite3


def supplier_email_domains(conn: sqlite3.Connection) -> frozenset[str]:
    """Return normalized supplier domains (lowercase, no @). Empty if table missing.

    Every ``domain_norm`` row counts, regardless of ``is_exclusion`` -- that column
    marks domains deduped out of a DeepSearch *new-opportunity* ranking because they
    were already present in OrigenLab's historical supplier base (see
    ``supplier_workbook._ingest_exclusion_rows``); it means "already a known
    supplier", not "safe to market to". It is used for a narrower, unrelated
    purpose elsewhere (``institution_grouping_audit``). Do not filter on it here --
    the marketing/outbound gate's contract (docs/OUTBOUND_SOURCE_OF_TRUTH.md) and
    tests/test_outbound_campaign_cli.py's strict-gate regression both rely on bare
    table membership blocking cold outreach.
    """
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='supplier_master' LIMIT 1"
    ).fetchone()
    if not row:
        return frozenset()
    rows = conn.execute(
        "SELECT lower(trim(domain_norm)) FROM supplier_master "
        "WHERE domain_norm IS NOT NULL AND trim(domain_norm) != ''"
    ).fetchall()
    return frozenset(str(r[0]) for r in rows if r[0])


def is_supplier_email_domain(email: str, supplier_domains: frozenset[str]) -> bool:
    e = (email or "").strip().lower()
    if "@" not in e or not supplier_domains:
        return False
    dom = e.rsplit("@", 1)[-1].strip()
    if not dom:
        return False
    if dom in supplier_domains:
        return True
    for d in supplier_domains:
        if dom.endswith("." + d):
            return True
    return False
