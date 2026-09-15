#!/usr/bin/env python3
"""Export the best N repeat-campaign candidates across OrigenLab contact sources.

This is the broad durable-campaign candidate builder. It unions the main contact
and prospect read models, normalizes/deduplicates by email, applies the campaign
safety gate, then ranks the eligible universe by buyer/contact evidence.

Historical Gmail Sent delivery and historical ``contacted``/``replied`` state
are not permanent blockers in this repeat-campaign lane. Hard blockers remain:
email/domain suppression, snoozed state, active CRM commercial holds, manual
``hold``/``inactive``, internal addresses, supplier domains and noise filters.

Candidate sources currently unioned:
- contact_master (broad Gmail/archive contact mart)
- commercial_identity_contact (+ account name)
- lead_master
- lead_contact_research resolved contacts
- lead_research_prospect active/non-blocked researched prospects
- outbound_campaign_recipient historical sent/replied recipients
- outreach_contact_state historical contacted/replied recipients

Raw transactional/email evidence tables are deliberately not scanned as separate
recipient pools because they feed the marts above and would re-introduce vendors,
automated systems, invoice counterparties and other non-prospect addresses.

Read-only on SQLite. Sends no email.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

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
    "contact_email",
    "recipient_name",
    "institution_name",
    "role",
    "sources",
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


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return bool(row)


def _norm_email(raw: object) -> str | None:
    value = str(raw or "").strip().lower()
    if not value or "@" not in value:
        return None
    # Candidate sources used here are already normalized/read-model fields. Keep
    # the first mailbox only if an accidental display wrapper slipped through.
    if "<" in value and ">" in value:
        value = value.split("<", 1)[1].split(">", 1)[0].strip()
    if " " in value or value.count("@") != 1:
        return None
    return value


def _new_candidate(email: str) -> dict[str, Any]:
    return {
        "contact_email": email,
        "recipient_name": "",
        "institution_name": "",
        "role": "",
        "sources": set(),
        "lead_fit_bucket": "",
        "lead_priority_score": 0.0,
        "prospect_score": 0.0,
        "total_emails": 0,
        "inbound_emails": 0,
        "outbound_emails": 0,
        "quote_email_count": 0,
        "invoice_email_count": 0,
        "purchase_email_count": 0,
        "business_doc_email_count": 0,
        "confidence_score": 0.0,
        "last_seen_at": "",
        "evidence_summary": "",
    }


def _cand(pool: dict[str, dict[str, Any]], raw_email: object) -> dict[str, Any] | None:
    email = _norm_email(raw_email)
    if not email:
        return None
    return pool.setdefault(email, _new_candidate(email))


def _prefer_text(c: dict[str, Any], key: str, value: object) -> None:
    text = str(value or "").strip()
    if text and (not c.get(key) or len(text) > len(str(c.get(key) or ""))):
        c[key] = text


def _max_num(c: dict[str, Any], key: str, value: object) -> None:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    if number > float(c.get(key) or 0):
        c[key] = number


def _fit_rank(value: str) -> int:
    return {"high_fit": 3, "medium_fit": 2, "low_fit": 1}.get((value or "").strip().lower(), 0)


def _load_pool(conn: sqlite3.Connection) -> tuple[dict[str, dict[str, Any]], Counter[str]]:
    pool: dict[str, dict[str, Any]] = {}
    source_rows: Counter[str] = Counter()

    if _table_exists(conn, "contact_master"):
        sql = """
        SELECT email, contact_name_best, organization_name_guess,
               total_emails, inbound_emails, outbound_emails,
               quote_email_count, invoice_email_count, purchase_email_count,
               business_doc_email_count, confidence_score, last_seen_at
        FROM contact_master
        WHERE email IS NOT NULL AND trim(email) != ''
        """
        for row in conn.execute(sql):
            source_rows["contact_master"] += 1
            c = _cand(pool, row[0])
            if not c:
                continue
            c["sources"].add("contact_master")
            _prefer_text(c, "recipient_name", row[1])
            _prefer_text(c, "institution_name", row[2])
            for key, value in zip(
                (
                    "total_emails", "inbound_emails", "outbound_emails",
                    "quote_email_count", "invoice_email_count", "purchase_email_count",
                    "business_doc_email_count", "confidence_score",
                ),
                row[3:11],
            ):
                _max_num(c, key, value)
            _prefer_text(c, "last_seen_at", row[11])

    if _table_exists(conn, "commercial_identity_contact"):
        sql = """
        SELECT c.normalized_email, c.display_name, c.role,
               COALESCE(a.canonical_name,''), c.identity_confidence, c.last_evidence_at
        FROM commercial_identity_contact c
        LEFT JOIN commercial_identity_account a ON a.account_id=c.account_id
        WHERE c.normalized_email IS NOT NULL AND trim(c.normalized_email) != ''
        """
        for row in conn.execute(sql):
            source_rows["commercial_identity_contact"] += 1
            c = _cand(pool, row[0])
            if not c:
                continue
            c["sources"].add("commercial_identity_contact")
            _prefer_text(c, "recipient_name", row[1])
            _prefer_text(c, "role", row[2])
            _prefer_text(c, "institution_name", row[3])
            conf = str(row[4] or "").strip().lower()
            if conf == "high":
                _max_num(c, "confidence_score", 1.0)
            elif conf == "medium":
                _max_num(c, "confidence_score", 0.7)
            elif conf == "low":
                _max_num(c, "confidence_score", 0.3)
            _prefer_text(c, "last_seen_at", row[5])

    if _table_exists(conn, "lead_master"):
        sql = """
        SELECT COALESCE(NULLIF(trim(email_norm),''), NULLIF(trim(email),'')),
               contact_name, org_name, fit_bucket, priority_score,
               evidence_summary, last_seen_at
        FROM lead_master
        WHERE COALESCE(NULLIF(trim(email_norm),''), NULLIF(trim(email),'')) IS NOT NULL
        """
        for row in conn.execute(sql):
            source_rows["lead_master"] += 1
            c = _cand(pool, row[0])
            if not c:
                continue
            c["sources"].add("lead_master")
            _prefer_text(c, "recipient_name", row[1])
            _prefer_text(c, "institution_name", row[2])
            fit = str(row[3] or "").strip().lower()
            if _fit_rank(fit) > _fit_rank(str(c.get("lead_fit_bucket") or "")):
                c["lead_fit_bucket"] = fit
            _max_num(c, "lead_priority_score", row[4])
            _prefer_text(c, "evidence_summary", row[5])
            _prefer_text(c, "last_seen_at", row[6])

    if _table_exists(conn, "lead_contact_research") and _table_exists(conn, "lead_master"):
        sql = """
        SELECT r.resolved_contact_email, lm.contact_name, lm.org_name,
               lm.fit_bucket, lm.priority_score, lm.evidence_summary, lm.last_seen_at
        FROM lead_contact_research r
        JOIN lead_master lm ON lm.id=r.lead_id
        WHERE r.resolved_contact_email IS NOT NULL
          AND trim(r.resolved_contact_email) != ''
        """
        for row in conn.execute(sql):
            source_rows["lead_contact_research"] += 1
            c = _cand(pool, row[0])
            if not c:
                continue
            c["sources"].add("lead_contact_research")
            _prefer_text(c, "recipient_name", row[1])
            _prefer_text(c, "institution_name", row[2])
            fit = str(row[3] or "").strip().lower()
            if _fit_rank(fit) > _fit_rank(str(c.get("lead_fit_bucket") or "")):
                c["lead_fit_bucket"] = fit
            _max_num(c, "lead_priority_score", row[4])
            _prefer_text(c, "evidence_summary", row[5])
            _prefer_text(c, "last_seen_at", row[6])

    if _table_exists(conn, "lead_research_prospect"):
        sql = """
        SELECT email, contact_name, organization_name, role_title,
               final_score, confidence, evidence_note, created_at
        FROM lead_research_prospect
        WHERE email IS NOT NULL AND trim(email) != ''
          AND COALESCE(is_active,1)=1
          AND COALESCE(is_blocked,0)=0
        """
        for row in conn.execute(sql):
            source_rows["lead_research_prospect"] += 1
            c = _cand(pool, row[0])
            if not c:
                continue
            c["sources"].add("lead_research_prospect")
            _prefer_text(c, "recipient_name", row[1])
            _prefer_text(c, "institution_name", row[2])
            _prefer_text(c, "role", row[3])
            _max_num(c, "prospect_score", row[4])
            conf = str(row[5] or "").strip().lower()
            if conf == "high":
                _max_num(c, "confidence_score", 1.0)
            elif conf == "medium":
                _max_num(c, "confidence_score", 0.7)
            elif conf == "low":
                _max_num(c, "confidence_score", 0.3)
            _prefer_text(c, "evidence_summary", row[6])
            _prefer_text(c, "last_seen_at", row[7])

    if _table_exists(conn, "outbound_campaign_recipient"):
        sql = """
        SELECT COALESCE(NULLIF(trim(email_norm),''), NULLIF(trim(email),'')),
               institution_name, state, sent_at
        FROM outbound_campaign_recipient
        WHERE COALESCE(NULLIF(trim(email_norm),''), NULLIF(trim(email),'')) IS NOT NULL
          AND lower(COALESCE(state,'')) IN ('sent','replied')
        """
        for row in conn.execute(sql):
            source_rows["outbound_campaign_recipient"] += 1
            c = _cand(pool, row[0])
            if not c:
                continue
            c["sources"].add("outbound_campaign_recipient")
            _prefer_text(c, "institution_name", row[1])
            _prefer_text(c, "last_seen_at", row[3])

    if _table_exists(conn, "outreach_contact_state"):
        sql = """
        SELECT contact_email_norm, state, last_contacted_at
        FROM outreach_contact_state
        WHERE contact_email_norm IS NOT NULL AND trim(contact_email_norm) != ''
          AND lower(state) IN ('contacted','replied')
        """
        for row in conn.execute(sql):
            source_rows["outreach_contact_state"] += 1
            c = _cand(pool, row[0])
            if not c:
                continue
            c["sources"].add("outreach_contact_state")
            _prefer_text(c, "last_seen_at", row[2])

    return pool, source_rows


def _manual_status_map(conn: sqlite3.Connection) -> dict[str, str]:
    if not _table_exists(conn, "manual_contact_status"):
        return {}
    rows = conn.execute("SELECT lower(trim(email_norm)), lower(trim(status)) FROM manual_contact_status").fetchall()
    return {str(e): str(s) for e, s in rows if e and s}


def _score(c: dict[str, Any]) -> float:
    score = 0.0
    sources = set(c.get("sources") or ())
    source_bonus = {
        "lead_master": 20,
        "lead_contact_research": 22,
        "lead_research_prospect": 18,
        "contact_master": 8,
        "commercial_identity_contact": 8,
        "outbound_campaign_recipient": 3,
        "outreach_contact_state": 2,
    }
    score += sum(source_bonus.get(s, 0) for s in sources)

    fit = str(c.get("lead_fit_bucket") or "").lower()
    score += {"high_fit": 35, "medium_fit": 18, "low_fit": -5}.get(fit, 0)
    score += min(40.0, max(0.0, float(c.get("lead_priority_score") or 0) * 5.0))
    score += min(25.0, max(0.0, float(c.get("prospect_score") or 0) * 0.25))

    score += min(18.0, float(c.get("quote_email_count") or 0) * 4.0)
    score += min(20.0, float(c.get("purchase_email_count") or 0) * 5.0)
    score += min(12.0, float(c.get("invoice_email_count") or 0) * 3.0)
    score += min(10.0, float(c.get("business_doc_email_count") or 0) * 2.0)
    score += min(10.0, math.log1p(float(c.get("inbound_emails") or 0)) * 2.0)
    score += min(8.0, math.log1p(float(c.get("total_emails") or 0)) * 1.5)

    conf = float(c.get("confidence_score") or 0)
    score += min(10.0, conf * 10.0 if conf <= 1.0 else conf)

    if str(c.get("institution_name") or "").strip():
        score += 4.0
    if str(c.get("recipient_name") or "").strip():
        score += 3.0
    if str(c.get("role") or "").strip():
        score += 2.0

    email = str(c.get("contact_email") or "").lower()
    local, _, domain = email.partition("@")
    role_text = f"{local} {c.get('role','')}".lower()
    positive = (
        "laboratorio", "lab", "investigacion", "research", "compras", "compra",
        "adquisicion", "adquisiciones", "procurement", "calidad", "quality",
    )
    if any(token in role_text for token in positive):
        score += 8.0
    if "proveedores" in role_text:
        score += 4.0
    if "oirs" in local:
        score -= 30.0
    elif local in {"info", "contacto", "direccion", "director", "secretaria"}:
        score -= 8.0

    free_domains = {
        "gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "icloud.com",
        "live.com", "msn.com",
    }
    score += -4.0 if domain in free_domains else 4.0

    last_seen = str(c.get("last_seen_at") or "")
    if last_seen.startswith("2026"):
        score += 5.0
    elif last_seen.startswith("2025"):
        score += 2.0

    return round(score, 3)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--summary-out", type=Path, default=None)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--limit", type=int, default=1000)
    ap.add_argument("--gmail-user", default=None)
    ap.add_argument("--sent-folder", action="append", default=[])
    args = ap.parse_args(argv)

    if args.limit <= 0:
        print("--limit must be a positive integer", file=sys.stderr)
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
                "Best-repeat export blocked: CRM commercial-hold snapshot missing. "
                "Run refresh_active_commercial_holds.py --apply first.",
                file=sys.stderr,
            )
            return 4

        gate_ctx = build_marketing_export_gate_context(
            conn,
            gmail_user=gmail_user,
            sent_folders=sent_folders,
            strict_contact_graph_noise=True,
            allow_prior_outreach_history=True,
        )
        manual_status = _manual_status_map(conn)
        pool, source_rows = _load_pool(conn)

        eligible: list[dict[str, Any]] = []
        blocked_reasons: Counter[str] = Counter()
        for c in pool.values():
            result = evaluate_campaign_eligibility(
                contact_email=str(c["contact_email"]),
                institution_name=str(c.get("institution_name") or "") or None,
                gate_ctx=gate_ctx,
                manual_status_by_email=manual_status,
            )
            if not result.eligible:
                blocked_reasons[result.reasons[0] if result.reasons else "unknown"] += 1
                continue
            c["score"] = _score(c)
            eligible.append(c)
    finally:
        conn.close()

    eligible.sort(
        key=lambda c: (
            -float(c.get("score") or 0),
            -float(c.get("lead_priority_score") or 0),
            -float(c.get("purchase_email_count") or 0),
            -float(c.get("quote_email_count") or 0),
            str(c.get("contact_email") or ""),
        )
    )
    selected = eligible[: int(args.limit)]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for idx, c in enumerate(selected, 1):
            row = dict(c)
            row["rank"] = idx
            row["sources"] = ";".join(sorted(c.get("sources") or ()))
            writer.writerow(row)

    summary_path = args.summary_out or args.out.with_name(args.out.stem + "_summary.json")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mode": "best_repeat_campaign_candidates",
        "sqlite_path": str(db_path),
        "gmail_user": gmail_user,
        "requested": int(args.limit),
        "raw_unique_candidate_emails": len(pool),
        "eligible_after_campaign_gate": len(eligible),
        "exported": len(selected),
        "source_rows": dict(sorted(source_rows.items())),
        "blocked_reason_counts": dict(sorted(blocked_reasons.items())),
        "policy": {
            "prior_gmail_sent_blocks": False,
            "historical_contacted_blocks": False,
            "historical_replied_blocks": False,
            "suppression_blocks": True,
            "snoozed_blocks": True,
            "active_commercial_engagement_blocks": True,
            "manual_hold_inactive_blocks": True,
            "supplier_internal_noise_blocks": True,
        },
        "out": str(args.out.resolve()),
    }
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
