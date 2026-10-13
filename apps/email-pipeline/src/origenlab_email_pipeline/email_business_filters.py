"""
Business-only filtering layer for the OrigenLab email pipeline.
Tags each email into operational buckets and provides filtered views.
Rules are transparent and inspectable via business_filter_rules.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.business_filter_rules import (
    BOUNCE_BODY_PATTERNS,
    BOUNCE_SENDER_PATTERNS,
    BOUNCE_SUBJECT_PATTERNS,
    BUSINESS_CORE_PATTERNS,
    CATEGORY_PRECEDENCE,
    COMMERCIAL_SUBTYPE_PATTERNS,
    INSTITUTION_BODY_PATTERNS,
    INSTITUTION_DOMAINS,
    INTERNAL_DOMAINS,
    LOGISTICS_DOMAINS,
    LOGISTICS_SUBJECT_PATTERNS,
    MARKETPLACE_DOMAINS,
    MARKETPLACE_SUBJECT_PATTERNS,
    NEWSLETTER_SENDER_PATTERNS,
    NEWSLETTER_SUBJECT_PATTERNS,
    SOCIAL_DOMAINS,
    SOCIAL_SUBJECT_PATTERNS,
    SPAM_SUBJECT_PATTERNS,
)

# Email extraction (same regex as scripts/reports/generate_client_report.py)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", re.I)


def _norm(s: str) -> str:
    return (s or "").lower().strip()


def _extract_emails(text: str) -> list[str]:
    return EMAIL_RE.findall(text or "")


def normalized_domain(addr_or_sender: str) -> str:
    """Extract primary domain from 'From' or 'email@domain.com'. Returns lowercased domain or empty."""
    addrs = _extract_emails(addr_or_sender or "")
    if not addrs:
        return ""
    return addrs[0].split("@")[-1].lower()


def recipient_domains(recipients: str) -> list[str]:
    """List of lowercased recipient domains from To/Cc string."""
    return [a.split("@")[-1].lower() for a in _extract_emails(recipients or "")]


def _matches_any(text: str, patterns: list[str]) -> bool:
    t = _norm(text)
    return any(p in t for p in patterns)


def _domain_in_list(domain: str, domain_list: list[str]) -> bool:
    d = _norm(domain)
    if not d:
        return False
    for x in domain_list:
        if d == x or d.endswith("." + x) or x in d:
            return True
    return False


# ---------------------------------------------------------------------------
# Per-category checks (return True if email matches that category)
# ---------------------------------------------------------------------------


def _is_bounce_ndr(sender: str, subject: str, body: str) -> bool:
    s, subj, b = _norm(sender), _norm(subject), _norm((body or "")[:2000])
    if _matches_any(s, BOUNCE_SENDER_PATTERNS):
        return True
    if _matches_any(subj, BOUNCE_SUBJECT_PATTERNS):
        return True
    if _matches_any(b, BOUNCE_BODY_PATTERNS):
        return True
    return False


def _is_spam_suspect(subject: str, body: str) -> bool:
    subj = _norm(subject)
    b = _norm((body or "")[:1500])
    return _matches_any(subj, SPAM_SUBJECT_PATTERNS) or _matches_any(b, SPAM_SUBJECT_PATTERNS)


def _is_social(sender: str, subject: str, sender_domain: str) -> bool:
    if _domain_in_list(sender_domain, SOCIAL_DOMAINS):
        return True
    return _matches_any(_norm(subject), SOCIAL_SUBJECT_PATTERNS)


def _is_newsletter(sender: str, subject: str) -> bool:
    s, subj = _norm(sender), _norm(subject)
    if _matches_any(s, NEWSLETTER_SENDER_PATTERNS):
        return True
    return _matches_any(subj, NEWSLETTER_SUBJECT_PATTERNS)


def _is_logistics(sender_domain: str, subject: str) -> bool:
    if _domain_in_list(sender_domain, LOGISTICS_DOMAINS):
        return True
    return _matches_any(_norm(subject), LOGISTICS_SUBJECT_PATTERNS)


def _is_marketplace(sender_domain: str, subject: str) -> bool:
    if _domain_in_list(sender_domain, MARKETPLACE_DOMAINS):
        return True
    return _matches_any(_norm(subject), MARKETPLACE_SUBJECT_PATTERNS)


def _is_institution(sender_domain: str, body: str) -> bool:
    if _domain_in_list(sender_domain, INSTITUTION_DOMAINS):
        return True
    return _matches_any(_norm((body or "")[:3000]), INSTITUTION_BODY_PATTERNS)


def _is_internal(sender_domain: str) -> bool:
    return _domain_in_list(sender_domain, INTERNAL_DOMAINS)


def _is_supplier(sender_domain: str) -> bool:
    from origenlab_email_pipeline.business_filter_rules import SUPPLIER_DOMAINS
    return bool(SUPPLIER_DOMAINS and _domain_in_list(sender_domain, SUPPLIER_DOMAINS))


def _is_customer(sender_domain: str) -> bool:
    from origenlab_email_pipeline.business_filter_rules import CUSTOMER_DOMAINS
    return bool(CUSTOMER_DOMAINS and _domain_in_list(sender_domain, CUSTOMER_DOMAINS))


def _is_business_core(subject: str, body: str) -> bool:
    blob = _norm((subject or "") + " " + (body or "")[:4000])
    return _matches_any(blob, BUSINESS_CORE_PATTERNS)


# ---------------------------------------------------------------------------
# Main classification: all matching tags + primary by precedence
# ---------------------------------------------------------------------------


def classify_email(
    *,
    sender: str | None = None,
    recipients: str | None = None,
    subject: str | None = None,
    body: str | None = None,
    from_: str | None = None,
    to: str | None = None,
    cc: str | None = None,
) -> dict[str, Any]:
    """
    Classify one email into tags and rollup flags.
    Accepts DB-style (sender, recipients, subject, body) or (from_, to, cc, subject, body).
    """
    from_ = from_ or sender or ""
    to = to or recipients or ""
    subj = subject or ""
    b = body or ""
    f"{to} {cc or ''}".strip()

    sender_dom = normalized_domain(from_)
    tags: list[str] = []

    if _is_bounce_ndr(from_, subj, b):
        tags.append("bounce_ndr")
    if _is_spam_suspect(subj, b):
        tags.append("spam_suspect")
    if _is_social(from_, subj, sender_dom):
        tags.append("social_notification")
    if _is_newsletter(from_, subj):
        tags.append("newsletter")
    if _is_logistics(sender_dom, subj):
        tags.append("logistics")
    if _is_marketplace(sender_dom, subj):
        tags.append("marketplace")
    if _is_institution(sender_dom, b):
        tags.append("institution")
    if _is_internal(sender_dom):
        tags.append("internal")
    if _is_supplier(sender_dom):
        tags.append("supplier")
    if _is_customer(sender_dom):
        tags.append("customer")
    if _is_business_core(subj, b):
        tags.append("business_core")

    if not tags:
        tags.append("unknown")

    # Primary category: first in precedence that appears in tags
    primary = "unknown"
    for cat in CATEGORY_PRECEDENCE:
        if cat in tags:
            primary = cat
            break

    # Optional commercial subtype (when primary is business_core)
    commercial_subtype: str | None = None
    if primary == "business_core":
        blob = _norm((subj or "") + " " + (b or "")[:2000])
        for subtype, patterns in COMMERCIAL_SUBTYPE_PATTERNS.items():
            if _matches_any(blob, patterns):
                commercial_subtype = subtype
                break

    # Rollup booleans
    business_include = {"business_core", "supplier", "customer", "institution", "logistics", "marketplace"}
    is_business_only_candidate = primary in business_include
    is_noise = primary in {"bounce_ndr", "spam_suspect", "social_notification", "newsletter"}
    is_operational = primary in business_include | {"internal"}
    is_marketing = "newsletter" in tags
    is_bounce = primary == "bounce_ndr"
    is_social = primary == "social_notification"
    is_internal = primary == "internal"

    out: dict[str, Any] = {
        "tags": tags,
        "primary_category": primary,
        "sender_domain": sender_dom or "(no address)",
        "is_business_only_candidate": is_business_only_candidate,
        "is_noise": is_noise,
        "is_operational": is_operational,
        "is_marketing": is_marketing,
        "is_bounce": is_bounce,
        "is_social": is_social,
        "is_internal": is_internal,
    }
    if commercial_subtype is not None:
        out["commercial_subtype"] = commercial_subtype
    return out


def row_to_classification(row: dict[str, Any] | Any) -> dict[str, Any]:
    """Accept a DB row (dict or Row) with sender, recipients, subject, body."""
    if hasattr(row, "keys"):
        return classify_email(
            sender=row.get("sender"),
            recipients=row.get("recipients"),
            subject=row.get("subject"),
            body=row.get("body"),
        )
    return classify_email(sender=row.get("sender"), recipients=row.get("recipients"), subject=row.get("subject"), body=row.get("body"))


# ---------------------------------------------------------------------------
# Filtered view predicates
# ---------------------------------------------------------------------------

# Views: all_messages, operational_no_ndr, business_only, business_only_external

def in_view_all_messages(_: dict) -> bool:
    return True


def in_view_operational_no_ndr(classification: dict) -> bool:
    """Exclude bounce_ndr only."""
    return not classification.get("is_bounce", False)


def in_view_business_only(classification: dict, include_internal: bool = True) -> bool:
    """Business-relevant only. By default includes internal."""
    if classification.get("is_noise"):
        return False
    if classification.get("is_business_only_candidate"):
        if not include_internal and classification.get("is_internal"):
            return False
        return True
    if include_internal and classification.get("is_internal"):
        return True
    return False


def in_view_business_only_external(classification: dict) -> bool:
    """Business-only, excluding internal mail."""
    return in_view_business_only(classification, include_internal=False)


def view_filter(classification: dict, view: str) -> bool:
    """Dispatch by view name."""
    if view == "all_messages":
        return in_view_all_messages(classification)
    if view == "operational_no_ndr":
        return in_view_operational_no_ndr(classification)
    if view == "business_only":
        return in_view_business_only(classification, include_internal=True)
    if view == "business_only_external":
        return in_view_business_only_external(classification)
    return False


# ---------------------------------------------------------------------------
# Batch: run filter pass over DB and return summary + samples
# ---------------------------------------------------------------------------

VIEW_NAMES = ["all_messages", "operational_no_ndr", "business_only", "business_only_external"]


def run_filter_pass(
    db_path: Path,
    limit: int | None,
    top_n: int,
    sample_size: int,
) -> tuple[dict, list[dict], dict[str, list[dict]]]:
    """
    Single pass over emails: classify, aggregate counts, per-view domain counts,
    and collect a sample of business_only rows.
    Returns (summary_dict, business_only_sample_list, domain_by_view).
    """
    import sqlite3
    from collections import Counter

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    primary_counts: Counter = Counter()
    rollup_counts: Counter = Counter()
    view_message_counts: Counter = Counter()
    domain_by_view: dict[str, Counter] = {v: Counter() for v in VIEW_NAMES}
    sender_exact_business: Counter = Counter()
    business_sample: list[dict] = []
    total = 0

    sql = "SELECT id, sender, recipients, subject, body FROM emails"
    params: tuple = ()
    if limit:
        sql += " ORDER BY id LIMIT ?"
        params = (limit,)

    cur = conn.execute(sql, params)
    for row in cur:
        total += 1
        r = dict(row)
        cl = row_to_classification(r)
        primary_counts[cl["primary_category"]] += 1
        rollup_counts["is_business_only_candidate"] += 1 if cl["is_business_only_candidate"] else 0
        rollup_counts["is_noise"] += 1 if cl["is_noise"] else 0
        rollup_counts["is_operational"] += 1 if cl["is_operational"] else 0
        rollup_counts["is_marketing"] += 1 if cl["is_marketing"] else 0
        rollup_counts["is_bounce"] += 1 if cl["is_bounce"] else 0
        rollup_counts["is_social"] += 1 if cl["is_social"] else 0
        rollup_counts["is_internal"] += 1 if cl["is_internal"] else 0

        for view in VIEW_NAMES:
            if view_filter(cl, view):
                view_message_counts[view] += 1
                dom = cl.get("sender_domain") or "(no address)"
                if dom != "(no address)":
                    domain_by_view[view][dom] += 1

        if view_filter(cl, "business_only"):
            sender_exact_business[(r.get("sender") or "")[:200]] += 1
            if len(business_sample) < sample_size:
                business_sample.append({
                    "id": r.get("id"),
                    "sender": (r.get("sender") or "")[:200],
                    "subject": (r.get("subject") or "")[:300],
                    "primary_category": cl["primary_category"],
                    "tags": cl["tags"],
                })

    conn.close()

    summary = {
        "total_classified": total,
        "primary_category_counts": dict(primary_counts),
        "rollup_counts": dict(rollup_counts),
        "view_counts": dict(view_message_counts),
        "top_sender_domains_all": [{"name": k, "count": v} for k, v in domain_by_view["all_messages"].most_common(top_n)],
        "top_sender_domains_operational_no_ndr": [{"name": k, "count": v} for k, v in domain_by_view["operational_no_ndr"].most_common(top_n)],
        "top_sender_domains_business_only": [{"name": k, "count": v} for k, v in domain_by_view["business_only"].most_common(top_n)],
        "top_sender_domains_business_only_external": [{"name": k, "count": v} for k, v in domain_by_view["business_only_external"].most_common(top_n)],
        "top_senders_business_only": [{"name": k, "count": v} for k, v in sender_exact_business.most_common(min(top_n, 50))],
    }

    domain_export = {
        v: [{"domain": k, "count": c} for k, c in domain_by_view[v].most_common(200)]
        for v in VIEW_NAMES
    }

    return summary, business_sample, domain_export
