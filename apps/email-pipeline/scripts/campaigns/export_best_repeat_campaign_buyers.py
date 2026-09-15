#!/usr/bin/env python3
"""Export the best N repeat-campaign *buyers* across OrigenLab contact sources.

This is the buyer-first companion to ``export_best_repeat_campaign_candidates.py``.
It reuses the broad multi-source pool and canonical repeat-campaign safety gate,
then adds commercial role evidence so vendors and sales/support mailboxes do not
win merely because they have lots of quote/purchase email traffic.

Additional buyer/supplier evidence:
- commercial_deal.client_contact_email / client_domain -> buyer boost
- commercial_deal.supplier_contact_email / supplier_domain -> supplier exclusion
  unless the same contact/domain also has explicit buyer evidence
- commercial_purchase_events.buyer_contact_email -> buyer boost
- commercial_procurement_signal.buyer_email_norm -> buyer boost
- functional seller/support local-parts (ventas, sales, billing, etc.) -> exclude
- optional per-domain cap (default 3) to avoid over-mailing one organization

Historical Gmail Sent and historical contacted/replied remain allowed. Canonical
hard blockers still apply: suppression, snooze, active commercial hold, manual
hold/inactive, internal domains, supplier_master domains and noise rules.

Read-only on SQLite. Sends no email.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

_BASE_PATH = Path(__file__).with_name("export_best_repeat_campaign_candidates.py")
_spec = importlib.util.spec_from_file_location("_origenlab_best_repeat_base", _BASE_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover
    raise RuntimeError(f"Unable to load base exporter: {_BASE_PATH}")
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)

from origenlab_email_pipeline.config import load_settings
from origenlab_email_pipeline.marketing_export_context import (
    active_commercial_hold_projection_ready,
    build_marketing_export_gate_context,
)
from origenlab_email_pipeline.outbound_campaign_gate import evaluate_campaign_eligibility
from origenlab_email_pipeline.outbound_core import (
    resolve_outbound_gmail_user,
    resolve_outbound_sent_folders,
)


OUTPUT_FIELDS = [
    "rank",
    "score",
    "buyer_score",
    "contact_email",
    "recipient_name",
    "institution_name",
    "role",
    "sources",
    "buyer_evidence",
    "commercial_client_count",
    "procurement_buyer_count",
    "purchase_buyer_count",
    "lead_fit_bucket",
    "lead_priority_score",
    "prospect_score",
    "total_emails",
    "inbound_emails",
    "outbound_emails",
    "quote_email_count",
    "invoice_email_count",
    "purchase_email_count",
    "business_doc_email_count",
    "confidence_score",
    "last_seen_at",
    "evidence_summary",
]

_SELLER_LOCAL_PARTS = {
    "ventas",
    "venta",
    "sales",
    "customerservice",
    "customer.service",
    "customer_service",
    "billing",
    "facturacion",
    "facturación",
    "marketing",
    "diseno",
    "diseño",
    "design",
    "webmaster",
    "orders",
    "order",
    "export",
    "exports",
    "logistics",
    "logistica",
    "logística",
}

_BUYER_LOCAL_TOKENS = (
    "lab",
    "laboratorio",
    "calidad",
    "quality",
    "compras",
    "compra",
    "adquisicion",
    "adquisiciones",
    "procurement",
    "investigacion",
    "investigación",
    "research",
    "cientif",
    "biotech",
    "biotecn",
)

_BUYER_ORG_TOKENS = (
    "universidad",
    "hospital",
    "clinica",
    "clínica",
    "instituto",
    "laboratorio",
    "centro de investig",
    "biotecn",
    "salud",
    "farm",
    "pharma",
    "veter",
    "agro",
    "alimentos",
    "minera",
    "mining",
    "quim",
    "quím",
)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return bool(row)


def _norm_email(raw: object) -> str | None:
    return _base._norm_email(raw)


def _norm_domain(raw: object) -> str | None:
    d = str(raw or "").strip().lower()
    if d.startswith("www."):
        d = d[4:]
    if "@" in d:
        d = d.rsplit("@", 1)[-1]
    if not d or " " in d or "." not in d:
        return None
    return d.rstrip(".")


def _domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in email else ""


def _commercial_role_evidence(conn: sqlite3.Connection) -> dict[str, Any]:
    client_emails: Counter[str] = Counter()
    supplier_emails: Counter[str] = Counter()
    client_domains: Counter[str] = Counter()
    supplier_domains: Counter[str] = Counter()
    procurement_buyers: Counter[str] = Counter()
    purchase_buyers: Counter[str] = Counter()

    if _table_exists(conn, "commercial_deal"):
        for client_email, client_domain, supplier_email, supplier_domain in conn.execute(
            """
            SELECT client_contact_email, client_domain,
                   supplier_contact_email, supplier_domain
            FROM commercial_deal
            """
        ):
            ce = _norm_email(client_email)
            se = _norm_email(supplier_email)
            cd = _norm_domain(client_domain)
            sd = _norm_domain(supplier_domain)
            if ce:
                client_emails[ce] += 1
                client_domains[_domain_of(ce)] += 1
            if cd:
                client_domains[cd] += 1
            if se:
                supplier_emails[se] += 1
                supplier_domains[_domain_of(se)] += 1
            if sd:
                supplier_domains[sd] += 1

    if _table_exists(conn, "commercial_procurement_signal"):
        for (raw,) in conn.execute(
            """
            SELECT buyer_email_norm
            FROM commercial_procurement_signal
            WHERE buyer_email_norm IS NOT NULL AND trim(buyer_email_norm) != ''
            """
        ):
            e = _norm_email(raw)
            if e:
                procurement_buyers[e] += 1

    if _table_exists(conn, "commercial_purchase_events"):
        for (raw,) in conn.execute(
            """
            SELECT buyer_contact_email
            FROM commercial_purchase_events
            WHERE buyer_contact_email IS NOT NULL AND trim(buyer_contact_email) != ''
            """
        ):
            e = _norm_email(raw)
            if e:
                purchase_buyers[e] += 1

    return {
        "client_emails": client_emails,
        "supplier_emails": supplier_emails,
        "client_domains": client_domains,
        "supplier_domains": supplier_domains,
        "procurement_buyers": procurement_buyers,
        "purchase_buyers": purchase_buyers,
    }


def _buyer_adjustment(c: dict[str, Any], roles: dict[str, Any]) -> tuple[float, list[str]]:
    email = str(c.get("contact_email") or "").lower()
    local = email.split("@", 1)[0] if "@" in email else email
    domain = _domain_of(email)
    role = str(c.get("role") or "").lower()
    org = str(c.get("institution_name") or "").lower()
    evidence = str(c.get("evidence_summary") or "").lower()
    text = f"{local} {role} {org} {evidence}"

    client_n = int(roles["client_emails"].get(email, 0))
    procurement_n = int(roles["procurement_buyers"].get(email, 0))
    purchase_n = int(roles["purchase_buyers"].get(email, 0))
    client_domain_n = int(roles["client_domains"].get(domain, 0))

    bonus = 0.0
    reasons: list[str] = []

    if client_n:
        bonus += min(40.0, 24.0 + 5.0 * client_n)
        reasons.append(f"commercial_client:{client_n}")
    if procurement_n:
        bonus += min(35.0, 24.0 + 3.0 * procurement_n)
        reasons.append(f"procurement_buyer:{procurement_n}")
    if purchase_n:
        bonus += min(30.0, 20.0 + 2.0 * purchase_n)
        reasons.append(f"purchase_buyer:{purchase_n}")
    if client_domain_n:
        bonus += min(12.0, 4.0 + 2.0 * client_domain_n)
        reasons.append(f"client_domain:{client_domain_n}")

    if any(tok in text for tok in _BUYER_LOCAL_TOKENS):
        bonus += 12.0
        reasons.append("buyer_role_or_local")
    if any(tok in org for tok in _BUYER_ORG_TOKENS):
        bonus += 8.0
        reasons.append("buyer_org_context")

    fit = str(c.get("lead_fit_bucket") or "").lower()
    if fit == "high_fit":
        bonus += 12.0
        reasons.append("high_fit")
    elif fit == "medium_fit":
        bonus += 5.0
        reasons.append("medium_fit")

    sources = set(c.get("sources") or ())
    if "lead_research_prospect" in sources:
        bonus += 8.0
        reasons.append("researched_prospect")
    if "lead_contact_research" in sources:
        bonus += 8.0
        reasons.append("researched_contact")

    return bonus, reasons


def _extra_block_reason(c: dict[str, Any], roles: dict[str, Any]) -> str | None:
    email = str(c.get("contact_email") or "").lower()
    local = email.split("@", 1)[0] if "@" in email else email
    domain = _domain_of(email)

    client_n = int(roles["client_emails"].get(email, 0))
    procurement_n = int(roles["procurement_buyers"].get(email, 0))
    purchase_n = int(roles["purchase_buyers"].get(email, 0))
    explicit_buyer = (client_n + procurement_n + purchase_n) > 0

    supplier_email_n = int(roles["supplier_emails"].get(email, 0))
    supplier_domain_n = int(roles["supplier_domains"].get(domain, 0))
    client_domain_n = int(roles["client_domains"].get(domain, 0))

    if supplier_email_n and not explicit_buyer:
        return "commercial_supplier_email"
    if supplier_domain_n and not client_domain_n and not explicit_buyer:
        return "commercial_supplier_domain"

    # Functional seller/support mailboxes are poor recipients for a buyer campaign.
    # A contact with explicit buyer evidence can still pass despite the local-part.
    if local in _SELLER_LOCAL_PARTS and not explicit_buyer:
        return "seller_or_support_mailbox"

    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--summary-out", type=Path, default=None)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--max-per-domain", type=int, default=3)
    ap.add_argument("--gmail-user", default=None)
    ap.add_argument("--sent-folder", action="append", default=[])
    args = ap.parse_args(argv)

    if args.limit <= 0:
        print("--limit must be positive", file=sys.stderr)
        return 2
    if args.max_per_domain < 0:
        print("--max-per-domain must be >= 0", file=sys.stderr)
        return 2

    settings = load_settings()
    db_path = (args.db or settings.resolved_sqlite_path()).expanduser().resolve()
    gmail_user = resolve_outbound_gmail_user(settings, explicit=args.gmail_user)
    sent_folders = resolve_outbound_sent_folders(args.sent_folder)

    if not db_path.is_file():
        print(f"SQLite database not found: {db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        if not active_commercial_hold_projection_ready(conn):
            print(
                "Buyer-first export blocked: CRM commercial-hold snapshot missing. "
                "Run refresh_active_commercial_holds.py --apply first.",
                file=sys.stderr,
            )
            return 4

        pool, source_rows = _base._load_pool(conn)
        roles = _commercial_role_evidence(conn)
        gate_ctx = build_marketing_export_gate_context(
            conn,
            gmail_user=gmail_user,
            sent_folders=sent_folders,
            allow_prior_outreach_history=True,
        )
        manual_status = _base._manual_status_map(conn)

        gate_blocked: Counter[str] = Counter()
        extra_blocked: Counter[str] = Counter()
        ranked: list[dict[str, Any]] = []

        for email, c in pool.items():
            gres = evaluate_campaign_eligibility(
                contact_email=email,
                institution_name=str(c.get("institution_name") or "") or None,
                gate_ctx=gate_ctx,
                manual_status_by_email=manual_status,
            )
            if not gres.eligible:
                gate_blocked[gres.reasons[0] if gres.reasons else "unknown"] += 1
                continue

            extra = _extra_block_reason(c, roles)
            if extra:
                extra_blocked[extra] += 1
                continue

            buyer_bonus, buyer_reasons = _buyer_adjustment(c, roles)
            base_score = float(_base._score(c))
            buyer_score = round(base_score + buyer_bonus, 3)

            row = dict(c)
            row["score"] = base_score
            row["buyer_score"] = buyer_score
            row["buyer_evidence"] = ";".join(buyer_reasons)
            row["commercial_client_count"] = int(roles["client_emails"].get(email, 0))
            row["procurement_buyer_count"] = int(roles["procurement_buyers"].get(email, 0))
            row["purchase_buyer_count"] = int(roles["purchase_buyers"].get(email, 0))
            row["sources"] = ";".join(sorted(set(c.get("sources") or ())))
            ranked.append(row)

        ranked.sort(
            key=lambda r: (
                -float(r.get("buyer_score") or 0),
                -float(r.get("score") or 0),
                str(r.get("contact_email") or ""),
            )
        )

        selected: list[dict[str, Any]] = []
        domain_counts: Counter[str] = Counter()
        domain_cap_dropped = 0
        for row in ranked:
            domain = _domain_of(str(row.get("contact_email") or ""))
            if args.max_per_domain and domain_counts[domain] >= int(args.max_per_domain):
                domain_cap_dropped += 1
                continue
            domain_counts[domain] += 1
            selected.append(row)
            if len(selected) >= int(args.limit):
                break
    finally:
        conn.close()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for i, row in enumerate(selected, 1):
            out = dict(row)
            out["rank"] = i
            writer.writerow(out)

    summary_path = args.summary_out or args.out.with_name(args.out.stem + "_summary.json")
    payload = {
        "schema_version": "2",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mode": "best_repeat_campaign_buyers",
        "sqlite_path": str(db_path),
        "gmail_user": gmail_user,
        "requested": int(args.limit),
        "max_per_domain": int(args.max_per_domain),
        "raw_unique_candidate_emails": len(pool),
        "eligible_after_all_filters_before_domain_cap": len(ranked),
        "exported": len(selected),
        "domain_cap_dropped_while_selecting": domain_cap_dropped,
        "source_rows": dict(sorted(source_rows.items())),
        "canonical_gate_blocked_reason_counts": dict(sorted(gate_blocked.items())),
        "buyer_filter_blocked_reason_counts": dict(sorted(extra_blocked.items())),
        "commercial_role_evidence": {
            "client_emails": len(roles["client_emails"]),
            "supplier_emails": len(roles["supplier_emails"]),
            "client_domains": len(roles["client_domains"]),
            "supplier_domains": len(roles["supplier_domains"]),
            "procurement_buyer_emails": len(roles["procurement_buyers"]),
            "purchase_buyer_emails": len(roles["purchase_buyers"]),
        },
        "policy": {
            "prior_gmail_sent_blocks": False,
            "historical_contacted_blocks": False,
            "historical_replied_blocks": False,
            "canonical_hard_blocks_preserved": True,
            "commercial_supplier_role_blocks": True,
            "seller_support_functional_mailbox_blocks": True,
            "commercial_client_and_buyer_evidence_boosts": True,
        },
        "out": str(args.out.resolve()),
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if len(selected) < int(args.limit):
        print(
            f"Warning: only {len(selected)} buyer-ranked candidates found for requested {args.limit}.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
