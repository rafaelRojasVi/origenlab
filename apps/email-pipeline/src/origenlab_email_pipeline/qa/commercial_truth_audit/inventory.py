"""Source inventory, overlap matrices, and identity conflict detection."""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from typing import Any

from origenlab_email_pipeline.qa.commercial_truth_audit.constants import (
    CONSUMER_EMAIL_DOMAINS,
    SOURCE_INVENTORY_FIELDS,
    SOURCE_NAMES,
)
from origenlab_email_pipeline.qa.commercial_truth_audit.readonly import safe_count, table_exists
from origenlab_email_pipeline.qa.commercial_truth_audit.redaction import email_domain, normalize_email


def _pct(n: int, d: int) -> float:
    if d <= 0:
        return 0.0
    return round(100.0 * n / d, 2)


def _unique_emails(values: list[str | None]) -> set[str]:
    return {e for e in (normalize_email(v) for v in values) if e}


def build_source_inventory(
    conn: sqlite3.Connection,
    prospects: list[dict[str, Any]],
    *,
    email_tier_counts: dict[str, int],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def blank(source_name: str, present: bool, **kwargs: Any) -> dict[str, Any]:
        base = {k: 0 if k not in {"source_name", "table_present", "last_source_refresh_timestamp", "notes"} else "" for k in SOURCE_INVENTORY_FIELDS}
        base["source_name"] = source_name
        base["table_present"] = int(present)
        base.update(kwargs)
        return base

    # lead_research_prospect
    present = table_exists(conn, "lead_research_prospect")
    emails = [p.get("email") for p in prospects]
    domains = [p.get("domain") for p in prospects]
    orgs = [p.get("organization_name") for p in prospects]
    rows.append(
        blank(
            "lead_research_prospect",
            present,
            total_rows=len(prospects),
            unique_emails=len(_unique_emails(emails)),
            unique_domains=len({str(d).lower() for d in domains if d}),
            unique_organizations=len({str(o).strip().lower() for o in orgs if o}),
            contacts_with_name=sum(1 for p in prospects if str(p.get("contact_name") or "").strip()),
            contacts_with_role=sum(1 for p in prospects if str(p.get("role_title") or "").strip()),
            contacts_with_product_interest=sum(
                1
                for p in prospects
                if str(p.get("product_angle") or p.get("likely_need") or p.get("top_equipment_tags") or "").strip()
            ),
            contacts_with_gmail_evidence=sum(
                1
                for p in prospects
                if int(p.get("gmail_sent_count") or 0) + int(p.get("gmail_received_count") or 0) > 0
                or p.get("has_origenlab_gmail_evidence")
            ),
            contacts_with_labdelivery_evidence=sum(1 for p in prospects if p.get("has_labdelivery_evidence")),
            contacts_with_quotation_evidence=sum(
                1 for p in prospects if int(p.get("quote_email_count") or 0) + int(p.get("quote_signal_count") or 0) > 0
            ),
            contacts_with_purchase_invoice_order_evidence=sum(
                1
                for p in prospects
                if int(p.get("purchase_email_count") or 0)
                + int(p.get("invoice_email_count") or 0)
                + int(p.get("procurement_signal_count") or 0)
                > 0
            ),
            contacts_with_tender_evidence=sum(1 for p in prospects if p.get("has_tender_evidence")),
            contacts_blocked_bounced_suppressed=sum(
                1
                for p in prospects
                if p.get("is_blocked")
                or p.get("audit_safety_state") in {"bounced", "suppressed", "blocked"}
            ),
            contacts_no_usable_email=sum(1 for p in prospects if not normalize_email(p.get("email"))),
            last_source_refresh_timestamp=_max_created(conn, "lead_research_prospect", "created_at"),
            notes="Operational overlay applied in audit rows; raw table rebuildable from research CSVs.",
        )
    )

    # contact_master
    present = table_exists(conn, "contact_master")
    cm_rows = []
    if present:
        cm_rows = [dict(r) for r in conn.execute("SELECT * FROM contact_master")]
    rows.append(
        blank(
            "contact_master",
            present,
            total_rows=len(cm_rows),
            unique_emails=len(_unique_emails([r.get("email") for r in cm_rows])),
            unique_domains=len({str(r.get("domain") or "").lower() for r in cm_rows if r.get("domain")}),
            unique_organizations=len(
                {str(r.get("organization_name_guess") or "").strip().lower() for r in cm_rows if r.get("organization_name_guess")}
            ),
            contacts_with_name=sum(1 for r in cm_rows if str(r.get("contact_name_best") or "").strip()),
            contacts_with_product_interest=sum(1 for r in cm_rows if str(r.get("top_equipment_tags") or "").strip()),
            contacts_with_quotation_evidence=sum(1 for r in cm_rows if int(r.get("quote_email_count") or 0) > 0),
            contacts_with_purchase_invoice_order_evidence=sum(
                1
                for r in cm_rows
                if int(r.get("purchase_email_count") or 0) + int(r.get("invoice_email_count") or 0) > 0
            ),
            contacts_no_usable_email=sum(1 for r in cm_rows if not normalize_email(r.get("email"))),
            last_source_refresh_timestamp=_max_created(conn, "contact_master", "last_seen_at"),
            notes="Derived business mart; rebuildable via build_business_mart.py.",
        )
    )

    # organization_master
    present = table_exists(conn, "organization_master")
    om_rows = []
    if present:
        om_rows = [dict(r) for r in conn.execute("SELECT * FROM organization_master")]
    rows.append(
        blank(
            "organization_master",
            present,
            total_rows=len(om_rows),
            unique_domains=len({str(r.get("domain") or "").lower() for r in om_rows if r.get("domain")}),
            unique_organizations=len(
                {str(r.get("organization_name_guess") or "").strip().lower() for r in om_rows if r.get("organization_name_guess")}
            ),
            contacts_with_product_interest=sum(1 for r in om_rows if str(r.get("top_equipment_tags") or "").strip()),
            contacts_with_quotation_evidence=sum(1 for r in om_rows if int(r.get("quote_email_count") or 0) > 0),
            contacts_with_purchase_invoice_order_evidence=sum(
                1
                for r in om_rows
                if int(r.get("purchase_email_count") or 0) + int(r.get("invoice_email_count") or 0) > 0
            ),
            last_source_refresh_timestamp=_max_created(conn, "organization_master", "last_seen_at"),
            notes="Domain-keyed org mart.",
        )
    )

    # emails tiers
    rows.append(
        blank(
            "emails_canonical_gmail",
            table_exists(conn, "emails"),
            total_rows=int(email_tier_counts.get("canonical_gmail") or 0),
            notes="source_file gmail:contacto@origenlab.cl/…",
        )
    )
    rows.append(
        blank(
            "emails_legacy_labdelivery",
            table_exists(conn, "emails"),
            total_rows=int(email_tier_counts.get("legacy_labdelivery") or 0),
            notes="source_file containing contacto@labdelivery",
        )
    )

    for table, note in (
        ("outreach_contact_state", "Outbound lifecycle sidecar (send-truth adjacent)."),
        ("contact_email_suppression", "Exact-email safety truth."),
        ("contact_domain_suppression", "Domain cold-outreach block."),
        ("commercial_contact_signal_rollup", "Rebuildable commercial intel rollup."),
        ("commercial_opportunity_fact", "Org-level opportunity facts."),
        ("commercial_deal", "Durable commercial deal records when present."),
        ("commercial_purchase_events", "Purchase event evidence when present."),
        ("external_leads_raw", "Imported research / DeepSearch raw rows when present."),
        ("lead_research_batch", "Prospect research batch metadata."),
    ):
        present = table_exists(conn, table)
        total = safe_count(conn, table) if present else 0
        extra: dict[str, Any] = {"total_rows": total, "notes": note}
        if present and table == "outreach_contact_state":
            extra["unique_emails"] = _count_distinct(conn, table, "contact_email_norm")
        if present and table == "contact_email_suppression":
            extra["unique_emails"] = _count_distinct(conn, table, "email")
            extra["contacts_blocked_bounced_suppressed"] = total
        if present and table == "contact_domain_suppression":
            extra["unique_domains"] = _count_distinct(conn, table, "domain_norm")
        if present and table == "commercial_contact_signal_rollup":
            extra["unique_emails"] = _count_distinct(conn, table, "contact_email")
            extra["unique_domains"] = _count_distinct(conn, table, "org_domain")
        if present and table == "lead_research_batch":
            extra["last_source_refresh_timestamp"] = _max_created(conn, table, "created_at")
        rows.append(blank(table, present, **extra))

    # Ensure every declared source appears once, sorted.
    by_name = {r["source_name"]: r for r in rows}
    ordered = [by_name[n] for n in SOURCE_NAMES if n in by_name]
    for name, row in sorted(by_name.items()):
        if name not in SOURCE_NAMES:
            ordered.append(row)
    return ordered


def build_source_overlap(prospects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Contact-level source co-occurrence matrix (boolean flags)."""
    flags = [
        ("in_prospect_table", lambda p: True),
        ("has_gmail", lambda p: bool(p.get("has_origenlab_gmail_evidence") or int(p.get("gmail_sent_count") or 0) + int(p.get("gmail_received_count") or 0) > 0)),
        ("has_labdelivery", lambda p: bool(p.get("has_labdelivery_evidence"))),
        ("has_quote", lambda p: int(p.get("quote_email_count") or 0) + int(p.get("quote_signal_count") or 0) > 0),
        ("has_purchase_invoice", lambda p: int(p.get("purchase_email_count") or 0) + int(p.get("invoice_email_count") or 0) > 0),
        ("has_tender", lambda p: bool(p.get("has_tender_evidence"))),
        ("is_suppressed_or_blocked", lambda p: p.get("audit_safety_state") in {"bounced", "suppressed", "blocked"}),
        ("in_contact_master", lambda p: int(p.get("quote_email_count") or 0) + int(p.get("invoice_email_count") or 0) + int(p.get("purchase_email_count") or 0) > 0 or bool(p.get("contact_name_best"))),
    ]
    counts: Counter[tuple[str, str]] = Counter()
    totals = Counter({name: 0 for name, _ in flags})
    for p in prospects:
        active = [name for name, fn in flags if fn(p)]
        for name in active:
            totals[name] += 1
        for i, a in enumerate(active):
            for b in active[i:]:
                counts[(a, b)] += 1
                if a != b:
                    counts[(b, a)] += 1
    out: list[dict[str, Any]] = []
    names = [n for n, _ in flags]
    for a in names:
        for b in names:
            both = counts[(a, b)] if a != b else totals[a]
            out.append(
                {
                    "source_a": a,
                    "source_b": b,
                    "intersection_contacts": both,
                    "source_a_total": totals[a],
                    "overlap_pct_of_a": _pct(both, totals[a]),
                }
            )
    out.sort(key=lambda r: (r["source_a"], r["source_b"]))
    return out


def build_account_identity_conflicts(prospects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Same domain → multiple org names; same org name → multiple domains."""
    domain_to_orgs: dict[str, set[str]] = defaultdict(set)
    org_to_domains: dict[str, set[str]] = defaultdict(set)
    for p in prospects:
        dom = str(p.get("domain") or "").strip().lower()
        org = str(p.get("organization_name") or "").strip()
        if not org:
            continue
        org_key = org.lower()
        if dom and dom not in CONSUMER_EMAIL_DOMAINS:
            domain_to_orgs[dom].add(org)
            org_to_domains[org_key].add(dom)
    rows: list[dict[str, Any]] = []
    for dom, orgs in sorted(domain_to_orgs.items()):
        if len(orgs) < 2:
            continue
        rows.append(
            {
                "conflict_type": "domain_multiple_org_names",
                "domain": dom,
                "organization_names": " | ".join(sorted(orgs)),
                "org_name_count": len(orgs),
                "domain_count": 1,
                "is_consumer_domain": False,
                "notes": "Do not auto-merge; report only.",
            }
        )
    for org_key, domains in sorted(org_to_domains.items()):
        if len(domains) < 2:
            continue
        rows.append(
            {
                "conflict_type": "org_name_multiple_domains",
                "domain": " | ".join(sorted(domains)),
                "organization_names": org_key,
                "org_name_count": 1,
                "domain_count": len(domains),
                "is_consumer_domain": False,
                "notes": "Possible alias or multi-site account; report only.",
            }
        )
    rows.sort(key=lambda r: (r["conflict_type"], r["domain"], r["organization_names"]))
    return rows


def build_contact_identity_conflicts(prospects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Same person-ish name with multiple emails; consumer-email join hazards."""
    name_to_emails: dict[str, set[str]] = defaultdict(set)
    email_to_orgs: dict[str, set[str]] = defaultdict(set)
    for p in prospects:
        em = normalize_email(p.get("email"))
        name = str(p.get("contact_name") or p.get("contact_name_best") or "").strip().lower()
        org = str(p.get("organization_name") or "").strip()
        if em and name and name not in {"", "n/a", "na", "contacto", "info"}:
            name_to_emails[name].add(em)
        if em and org:
            email_to_orgs[em].add(org)
    rows: list[dict[str, Any]] = []
    for name, emails in sorted(name_to_emails.items()):
        if len(emails) < 2:
            continue
        rows.append(
            {
                "conflict_type": "person_multiple_emails",
                "contact_name": name,
                "emails_redacted_count": len(emails),
                "domains": " | ".join(sorted({email_domain(e) or "" for e in emails})),
                "is_consumer_email_involved": any((email_domain(e) or "") in CONSUMER_EMAIL_DOMAINS for e in emails),
                "notes": "Same display name across addresses; do not auto-collapse.",
            }
        )
    for p in prospects:
        em = normalize_email(p.get("email"))
        dom = email_domain(em)
        if not em or not dom or dom not in CONSUMER_EMAIL_DOMAINS:
            continue
        rows.append(
            {
                "conflict_type": "consumer_email_domain_join_unsafe",
                "contact_name": str(p.get("contact_name") or ""),
                "emails_redacted_count": 1,
                "domains": dom,
                "is_consumer_email_involved": True,
                "notes": "Cannot safely join account by domain for consumer mailbox.",
            }
        )
    # Deduplicate consumer rows by domain+name.
    dedup: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in rows:
        key = (r["conflict_type"], r.get("contact_name") or "", r.get("domains") or "")
        dedup[key] = r
    out = list(dedup.values())
    out.sort(key=lambda r: (r["conflict_type"], r.get("contact_name") or "", r.get("domains") or ""))
    return out


def _max_created(conn: sqlite3.Connection, table: str, col: str) -> str:
    if not table_exists(conn, table):
        return ""
    try:
        row = conn.execute(f"SELECT MAX({col}) FROM {table}").fetchone()
    except sqlite3.Error:
        return ""
    return str(row[0] or "") if row else ""


def _count_distinct(conn: sqlite3.Connection, table: str, col: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(DISTINCT {col}) FROM {table}").fetchone()[0])
    except sqlite3.Error:
        return 0
