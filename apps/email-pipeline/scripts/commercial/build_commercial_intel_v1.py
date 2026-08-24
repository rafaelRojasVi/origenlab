#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# SAFETY (break-glass): Rebuild / incremental paths DELETE and rewrite commercial_*
# and related rollup tables. Can remove large amounts of derived data before rebuild.
# See docs/SCRIPT_MAP.md — "Break-glass scripts".
# -----------------------------------------------------------------------------
"""Build v1 commercial-intelligence layer (signals + candidates).

Design:
- Raw archive tables stay untouched.
- Rebuildable layer: commercial_*_fact/rollup tables.
- Durable layer: *_candidate + review/override tables.
- Watermark is performance-only; correctness uses idempotent rewrites for selected emails.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import sqlite3
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from origenlab_email_pipeline.business_mart import domain_of, primary_sender_email
from origenlab_email_pipeline.commercial.commercial_intel_queries import (
    derive_existing_client_domains,
    derive_internal_domains,
    derive_vendor_domains,
    fetch_candidate_suppressed_counts,
    fetch_commercial_email_signal_fact_rows,
    fetch_contact_rollup_for_contact_candidates,
    fetch_emails_for_commercial_build,
    fetch_opportunity_fact_for_opp_candidates,
    fetch_org_rollup_for_opportunity_insert,
    fetch_org_rollup_for_org_candidates,
)
from origenlab_email_pipeline.commercial.commercial_intel_rules import (
    derive_email_signal_facts,
    pick_external_contact,
)
from origenlab_email_pipeline.timeutil import now_iso
from origenlab_email_pipeline.config import load_settings
from origenlab_email_pipeline.db import connect
from origenlab_email_pipeline.pipeline_run_recorder import finish_run, get_kv, set_kv, start_run
from origenlab_email_pipeline.sqlite_migrate import SchemaLayer, migrate_sqlite_schema

WATERMARK_KEY = "commercial_v1_last_email_id"
SUMMARY_KEY = "commercial_v1_last_summary"


def _clear_rebuildable(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DELETE FROM commercial_opportunity_fact;
        DELETE FROM commercial_contact_signal_rollup;
        DELETE FROM commercial_org_signal_rollup;
        DELETE FROM commercial_email_signal_fact;
        """
    )
    conn.commit()


def _delete_existing_facts_for_emails(conn: sqlite3.Connection, email_ids: list[int]) -> None:
    if not email_ids:
        return
    step = 500
    for i in range(0, len(email_ids), step):
        chunk = email_ids[i : i + step]
        placeholders = ",".join("?" for _ in chunk)
        conn.execute(f"DELETE FROM commercial_email_signal_fact WHERE email_id IN ({placeholders})", chunk)
    conn.commit()


def _build_email_facts(
    conn: sqlite3.Connection,
    *,
    rows: list[sqlite3.Row],
    run_id: int,
    internal_domains: set[str],
    vendor_domains: set[str],
    existing_client_domains: set[str],
) -> dict[str, int]:
    inserted = 0
    suppressed = 0
    by_reason: Counter[str] = Counter()

    payloads: list[tuple[object, ...]] = []
    for r in rows:
        sender_raw = r["sender"] or ""
        recipients_raw = r["recipients"] or ""
        sender_email = primary_sender_email(sender_raw)
        sender_domain = domain_of(sender_email)
        contact_email, contact_domain = pick_external_contact(
            sender_raw=sender_raw,
            recipients_raw=recipients_raw,
            internal_domains=internal_domains,
        )
        org_domain = contact_domain or sender_domain or ""
        facts = derive_email_signal_facts(
            subject=r["subject"] or "",
            sender_raw=sender_raw,
            recipients_raw=recipients_raw,
            top_reply_clean=r["top_reply_clean"] or "",
            full_body_clean=r["full_body_clean"] or "",
            sender_domain=sender_domain,
            internal_domains=internal_domains,
            vendor_domains=vendor_domains,
            existing_client_domains=existing_client_domains,
        )
        for fact in facts:
            inserted += 1
            by_reason[fact.reason_code] += 1
            if fact.signal_kind == "suppression":
                suppressed += 1
            payloads.append(
                (
                    int(r["id"]),
                    r["source_file"] or "",
                    r["date_iso"] or "",
                    sender_email or "",
                    sender_domain or "",
                    contact_email or "",
                    contact_domain or "",
                    org_domain,
                    fact.signal_code,
                    fact.signal_kind,
                    fact.reason_code,
                    fact.reason_text,
                    float(fact.confidence_score),
                    float(fact.strength_score),
                    fact.rationale_json,
                    run_id,
                    now_iso(),
                )
            )
    if payloads:
        conn.executemany(
            """
            INSERT OR REPLACE INTO commercial_email_signal_fact
            (email_id, source_file, sent_at, sender_email, sender_domain, contact_email, contact_domain, org_domain,
             signal_code, signal_kind, reason_code, reason_text, confidence_score, strength_score, rationale_json,
             run_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payloads,
        )
        conn.commit()
    return {
        "signal_rows": inserted,
        "suppression_rows": suppressed,
        "distinct_reason_codes": len(by_reason),
    }


def _safe_mean(nums: list[float]) -> float:
    if not nums:
        return 0.0
    return float(sum(nums) / len(nums))


def _rebuild_rollups(conn: sqlite3.Connection, *, run_id: int) -> dict[str, int]:
    rows = fetch_commercial_email_signal_fact_rows(conn)

    org_acc: dict[str, dict[str, object]] = defaultdict(
        lambda: {
            "first_seen_at": None,
            "last_seen_at": None,
            "email_ids": set(),
            "positive": 0,
            "suppression": 0,
            "pos_reasons": Counter(),
            "sup_reasons": Counter(),
            "signal_counts": Counter(),
            "conf": [],
            "strength": [],
        }
    )
    contact_acc: dict[str, dict[str, object]] = defaultdict(
        lambda: {
            "org_domain": "",
            "first_seen_at": None,
            "last_seen_at": None,
            "email_ids": set(),
            "positive": 0,
            "suppression": 0,
            "pos_reasons": Counter(),
            "sup_reasons": Counter(),
            "signal_counts": Counter(),
            "conf": [],
            "strength": [],
        }
    )

    for email_id, sent_at, contact_email, org_domain, signal_code, signal_kind, reason_code, conf, strength in rows:
        org = (org_domain or "").strip().lower()
        contact = (contact_email or "").strip().lower()
        if org:
            a = org_acc[org]
            a["email_ids"].add(int(email_id))
            if sent_at:
                if not a["first_seen_at"] or sent_at < a["first_seen_at"]:
                    a["first_seen_at"] = sent_at
                if not a["last_seen_at"] or sent_at > a["last_seen_at"]:
                    a["last_seen_at"] = sent_at
            if signal_kind == "positive":
                a["positive"] += 1
                a["pos_reasons"][reason_code] += 1
            else:
                a["suppression"] += 1
                a["sup_reasons"][reason_code] += 1
            a["signal_counts"][signal_code] += 1
            a["conf"].append(float(conf or 0.0))
            a["strength"].append(float(strength or 0.0))

        if contact:
            c = contact_acc[contact]
            if org and not c["org_domain"]:
                c["org_domain"] = org
            c["email_ids"].add(int(email_id))
            if sent_at:
                if not c["first_seen_at"] or sent_at < c["first_seen_at"]:
                    c["first_seen_at"] = sent_at
                if not c["last_seen_at"] or sent_at > c["last_seen_at"]:
                    c["last_seen_at"] = sent_at
            if signal_kind == "positive":
                c["positive"] += 1
                c["pos_reasons"][reason_code] += 1
            else:
                c["suppression"] += 1
                c["sup_reasons"][reason_code] += 1
            c["signal_counts"][signal_code] += 1
            c["conf"].append(float(conf or 0.0))
            c["strength"].append(float(strength or 0.0))

    conn.execute("DELETE FROM commercial_org_signal_rollup")
    conn.execute("DELETE FROM commercial_contact_signal_rollup")
    conn.execute("DELETE FROM commercial_opportunity_fact")

    org_payload = []
    for org_domain, a in org_acc.items():
        email_count = len(a["email_ids"])
        sup_count = int(a["suppression"])
        pos_count = int(a["positive"])
        is_supp = 1 if sup_count > pos_count else 0
        org_payload.append(
            (
                org_domain,
                a["first_seen_at"] or "",
                a["last_seen_at"] or "",
                email_count,
                pos_count,
                sup_count,
                ",".join(k for k, _ in a["sup_reasons"].most_common(8)),
                ",".join(k for k, _ in a["pos_reasons"].most_common(8)),
                int(a["signal_counts"].get("quote_intent", 0)),
                int(a["signal_counts"].get("procurement_intent", 0)),
                int(a["signal_counts"].get("technical_inquiry", 0)),
                email_count if email_count >= 3 else 0,
                int(a["signal_counts"].get("invoice_payment_suppression", 0)),
                int(a["signal_counts"].get("logistics_suppression", 0)),
                int(a["signal_counts"].get("vendor_suppression", 0)),
                int(a["signal_counts"].get("existing_client_suppression", 0)),
                _safe_mean(a["conf"]),
                _safe_mean(a["strength"]),
                is_supp,
                "suppressed_by_signal_balance" if is_supp else "",
                run_id,
                now_iso(),
            )
        )
    conn.executemany(
        """
        INSERT INTO commercial_org_signal_rollup
        (org_domain, first_seen_at, last_seen_at, evidence_email_count, positive_signal_count,
         suppression_signal_count, suppression_reason_codes, positive_reason_codes, quote_signal_count,
         procurement_signal_count, technical_signal_count, repeated_interaction_count,
         invoice_or_payment_signal_count, logistics_signal_count, vendor_like_signal_count,
         existing_client_signal_count, confidence_score, strength_score, is_suppressed,
         suppression_summary, run_id, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        org_payload,
    )

    contact_payload = []
    for contact_email, c in contact_acc.items():
        email_count = len(c["email_ids"])
        sup_count = int(c["suppression"])
        pos_count = int(c["positive"])
        is_supp = 1 if sup_count > pos_count else 0
        contact_payload.append(
            (
                contact_email,
                c["org_domain"] or "",
                c["first_seen_at"] or "",
                c["last_seen_at"] or "",
                email_count,
                pos_count,
                sup_count,
                ",".join(k for k, _ in c["sup_reasons"].most_common(8)),
                ",".join(k for k, _ in c["pos_reasons"].most_common(8)),
                int(c["signal_counts"].get("quote_intent", 0)),
                int(c["signal_counts"].get("procurement_intent", 0)),
                int(c["signal_counts"].get("technical_inquiry", 0)),
                email_count if email_count >= 3 else 0,
                int(c["signal_counts"].get("invoice_payment_suppression", 0)),
                int(c["signal_counts"].get("logistics_suppression", 0)),
                int(c["signal_counts"].get("vendor_suppression", 0)),
                int(c["signal_counts"].get("existing_client_suppression", 0)),
                _safe_mean(c["conf"]),
                _safe_mean(c["strength"]),
                is_supp,
                "suppressed_by_signal_balance" if is_supp else "",
                run_id,
                now_iso(),
            )
        )
    conn.executemany(
        """
        INSERT INTO commercial_contact_signal_rollup
        (contact_email, org_domain, first_seen_at, last_seen_at, evidence_email_count, positive_signal_count,
         suppression_signal_count, suppression_reason_codes, positive_reason_codes, quote_signal_count,
         procurement_signal_count, technical_signal_count, repeated_interaction_count,
         invoice_or_payment_signal_count, logistics_signal_count, vendor_like_signal_count,
         existing_client_signal_count, confidence_score, strength_score, is_suppressed,
         suppression_summary, run_id, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        contact_payload,
    )

    opportunity_rows = fetch_org_rollup_for_opportunity_insert(conn)
    opportunity_payload = []
    for (
        org_domain,
        quote_count,
        procurement_count,
        tech_count,
        repeated_count,
        conf,
        strength,
        is_supp,
        sup_reasons,
        evidence_count,
    ) in opportunity_rows:
        top_codes = []
        if quote_count:
            top_codes.append("quote_intent")
        if procurement_count:
            top_codes.append("procurement_intent")
        if tech_count:
            top_codes.append("technical_inquiry")
        if repeated_count:
            top_codes.append("repeated_interaction")
        opportunity_payload.append(
            (
                f"org:{org_domain}",
                org_domain,
                None,
                ",".join(top_codes[:5]),
                int(evidence_count),
                int((quote_count or 0) + (procurement_count or 0) + (tech_count or 0)),
                int((1 if is_supp else 0)),
                float(conf or 0.0),
                float(strength or 0.0),
                int(is_supp or 0),
                str(sup_reasons or ""),
                '{"source":"commercial_org_signal_rollup"}',
                run_id,
                now_iso(),
            )
        )
    conn.executemany(
        """
        INSERT INTO commercial_opportunity_fact
        (opportunity_key, org_domain, top_contact_email, top_signal_codes, evidence_email_count,
         positive_signal_count, suppression_signal_count, confidence_score, strength_score, is_suppressed,
         suppression_summary, rationale_json, run_id, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        opportunity_payload,
    )
    conn.commit()
    return {
        "org_rollup_rows": len(org_payload),
        "contact_rollup_rows": len(contact_payload),
        "opportunity_fact_rows": len(opportunity_payload),
    }


def _load_status_map(conn: sqlite3.Connection, table: str, key_col: str) -> dict[str, str]:
    rows = conn.execute(f"SELECT {key_col}, status FROM {table}").fetchall()
    return {str(r[0]): str(r[1]) for r in rows if r and r[0]}


def _persist_candidates(conn: sqlite3.Connection, *, run_id: int) -> dict[str, int]:
    old_org = _load_status_map(conn, "organization_candidate", "org_domain")
    old_contact = _load_status_map(conn, "contact_candidate", "contact_email")
    old_opp = _load_status_map(conn, "opportunity_candidate", "opportunity_key")

    org_rows = fetch_org_rollup_for_org_candidates(conn)
    for domain, conf, strength, evidence, last_seen, sup_codes, is_supp, pos_count in org_rows:
        base_status = "suppressed" if int(is_supp or 0) else ("needs_review" if int(pos_count or 0) >= 2 else "new")
        conn.execute(
            """
            INSERT INTO organization_candidate
            (org_domain, display_name, candidate_type, status, confidence_score, strength_score, evidence_count,
             latest_activity_at, suppression_flags, rationale_text, provenance_json, created_at, updated_at)
            VALUES (?, ?, 'net_new', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(org_domain) DO UPDATE SET
              display_name=excluded.display_name,
              confidence_score=excluded.confidence_score,
              strength_score=excluded.strength_score,
              evidence_count=excluded.evidence_count,
              latest_activity_at=excluded.latest_activity_at,
              suppression_flags=excluded.suppression_flags,
              rationale_text=excluded.rationale_text,
              provenance_json=excluded.provenance_json,
              status=CASE
                WHEN organization_candidate.status IN ('approved','rejected','snoozed') THEN organization_candidate.status
                ELSE excluded.status
              END,
              updated_at=excluded.updated_at
            """,
            (
                domain,
                domain,
                base_status,
                float(conf or 0.0),
                float(strength or 0.0),
                int(evidence or 0),
                last_seen or "",
                sup_codes or "",
                "Derived from commercial org rollup v1.",
                '{"source":"commercial_org_signal_rollup"}',
                now_iso(),
                now_iso(),
            ),
        )

    contact_rows = fetch_contact_rollup_for_contact_candidates(conn)
    for contact, org_domain, conf, strength, evidence, last_seen, sup_codes, is_supp, pos_count in contact_rows:
        base_status = "suppressed" if int(is_supp or 0) else ("needs_review" if int(pos_count or 0) >= 2 else "new")
        conn.execute(
            """
            INSERT INTO contact_candidate
            (contact_email, org_domain, display_name, status, confidence_score, strength_score, evidence_count,
             latest_activity_at, suppression_flags, rationale_text, provenance_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(contact_email) DO UPDATE SET
              org_domain=excluded.org_domain,
              display_name=excluded.display_name,
              confidence_score=excluded.confidence_score,
              strength_score=excluded.strength_score,
              evidence_count=excluded.evidence_count,
              latest_activity_at=excluded.latest_activity_at,
              suppression_flags=excluded.suppression_flags,
              rationale_text=excluded.rationale_text,
              provenance_json=excluded.provenance_json,
              status=CASE
                WHEN contact_candidate.status IN ('approved','rejected','snoozed') THEN contact_candidate.status
                ELSE excluded.status
              END,
              updated_at=excluded.updated_at
            """,
            (
                contact,
                org_domain or "",
                contact,
                base_status,
                float(conf or 0.0),
                float(strength or 0.0),
                int(evidence or 0),
                last_seen or "",
                sup_codes or "",
                "Derived from commercial contact rollup v1.",
                '{"source":"commercial_contact_signal_rollup"}',
                now_iso(),
                now_iso(),
            ),
        )

    opp_rows = fetch_opportunity_fact_for_opp_candidates(conn)
    for key, org_domain, conf, strength, evidence, is_supp, sup_summary, top_codes in opp_rows:
        base_status = "suppressed" if int(is_supp or 0) else ("needs_review" if float(conf or 0.0) >= 0.55 else "new")
        conn.execute(
            """
            INSERT INTO opportunity_candidate
            (opportunity_key, org_domain, status, confidence_score, strength_score, evidence_count,
             latest_activity_at, suppression_flags, rationale_text, provenance_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(opportunity_key) DO UPDATE SET
              org_domain=excluded.org_domain,
              confidence_score=excluded.confidence_score,
              strength_score=excluded.strength_score,
              evidence_count=excluded.evidence_count,
              latest_activity_at=excluded.latest_activity_at,
              suppression_flags=excluded.suppression_flags,
              rationale_text=excluded.rationale_text,
              provenance_json=excluded.provenance_json,
              status=CASE
                WHEN opportunity_candidate.status IN ('approved','rejected','snoozed') THEN opportunity_candidate.status
                ELSE excluded.status
              END,
              updated_at=excluded.updated_at
            """,
            (
                key,
                org_domain,
                base_status,
                float(conf or 0.0),
                float(strength or 0.0),
                int(evidence or 0),
                now_iso(),
                sup_summary or "",
                f"Top signals: {top_codes or ''}",
                '{"source":"commercial_opportunity_fact"}',
                now_iso(),
                now_iso(),
            ),
        )
    conn.commit()

    _apply_active_overrides(conn)
    event_rows = _write_status_change_events(conn, old_org, old_contact, old_opp, run_id)
    return {
        "org_candidates": conn.execute("SELECT COUNT(*) FROM organization_candidate").fetchone()[0],
        "contact_candidates": conn.execute("SELECT COUNT(*) FROM contact_candidate").fetchone()[0],
        "opportunity_candidates": conn.execute("SELECT COUNT(*) FROM opportunity_candidate").fetchone()[0],
        "review_events_written": event_rows,
    }


def _apply_active_overrides(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        """
        SELECT entity_kind, entity_key, override_code, override_value
        FROM candidate_manual_override
        WHERE is_active = 1
        """
    ).fetchall()
    for entity_kind, entity_key, code, value in rows:
        if entity_kind == "organization":
            table, key_col = "organization_candidate", "org_domain"
        elif entity_kind == "contact":
            table, key_col = "contact_candidate", "contact_email"
        elif entity_kind == "opportunity":
            table, key_col = "opportunity_candidate", "opportunity_key"
        else:
            continue
        if code == "force_status":
            conn.execute(f"UPDATE {table} SET status = ?, updated_at = ? WHERE {key_col} = ?", (value, now_iso(), entity_key))
        elif code == "force_suppress":
            conn.execute(
                f"UPDATE {table} SET status = 'suppressed', suppression_flags = ?, updated_at = ? WHERE {key_col} = ?",
                (value, now_iso(), entity_key),
            )
        elif code == "unsuppress":
            conn.execute(
                f"UPDATE {table} SET status = CASE WHEN status = 'suppressed' THEN 'needs_review' ELSE status END, updated_at = ? "
                f"WHERE {key_col} = ?",
                (now_iso(), entity_key),
            )
    conn.commit()


def _write_status_change_events(
    conn: sqlite3.Connection,
    old_org: dict[str, str],
    old_contact: dict[str, str],
    old_opp: dict[str, str],
    run_id: int,
) -> int:
    events: list[tuple[object, ...]] = []
    for domain, status in _load_status_map(conn, "organization_candidate", "org_domain").items():
        prev = old_org.get(domain)
        if prev is not None and prev != status:
            events.append(("organization", domain, prev, status, "SYSTEM_SYNC", "Status updated by v1 sync.", "", "system", run_id, now_iso()))
    for contact, status in _load_status_map(conn, "contact_candidate", "contact_email").items():
        prev = old_contact.get(contact)
        if prev is not None and prev != status:
            events.append(("contact", contact, prev, status, "SYSTEM_SYNC", "Status updated by v1 sync.", "", "system", run_id, now_iso()))
    for key, status in _load_status_map(conn, "opportunity_candidate", "opportunity_key").items():
        prev = old_opp.get(key)
        if prev is not None and prev != status:
            events.append(("opportunity", key, prev, status, "SYSTEM_SYNC", "Status updated by v1 sync.", "", "system", run_id, now_iso()))
    if events:
        conn.executemany(
            """
            INSERT INTO candidate_review_event
            (entity_kind, entity_key, previous_status, next_status, reason_code, reason_text, note_text, actor, run_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            events,
        )
        conn.commit()
    return len(events)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rebuild", action="store_true", help="Clear rebuildable commercial tables and recompute.")
    ap.add_argument("--reprocess-days", type=int, default=None, help="Also reprocess recent days (plus > watermark).")
    ap.add_argument("--internal-domain", action="append", default=[], help="Repeatable internal domain override.")
    ap.add_argument("--vendor-domain", action="append", default=[], help="Repeatable vendor suppression domain.")
    ap.add_argument("--existing-client-domain", action="append", default=[], help="Repeatable existing-client domain.")
    args = ap.parse_args()

    settings = load_settings()
    conn = connect(settings.resolved_sqlite_path())
    migrate_sqlite_schema(conn, layers={SchemaLayer.ARCHIVE_AND_MART, SchemaLayer.COMMERCIAL_INTEL})
    run_id = start_run(conn, script_name="scripts/commercial/build_commercial_intel_v1.py", notes="commercial-intel-v1")

    try:
        stage_t0 = time.monotonic()
        internal_domains = derive_internal_domains(conn) | {
            d.strip().lower()
            for d in args.internal_domain
            if d.strip()
        }
        vendor_domains = {d.strip().lower() for d in args.vendor_domain if d.strip()} | derive_vendor_domains(conn)
        existing_client_domains = {d.strip().lower() for d in args.existing_client_domain if d.strip()} | derive_existing_client_domains(conn)
        print(f"[timing] commercial_domain_derivation_seconds={time.monotonic() - stage_t0:.2f}")

        if args.rebuild:
            _clear_rebuildable(conn)
            last_watermark = 0
        else:
            raw_wm = get_kv(conn, WATERMARK_KEY) or "0"
            try:
                last_watermark = int(raw_wm)
            except ValueError:
                last_watermark = 0

        stage_t0 = time.monotonic()
        rows = fetch_emails_for_commercial_build(
            conn,
            rebuild=args.rebuild,
            last_watermark=last_watermark,
            reprocess_days=args.reprocess_days,
        )
        print(f"[timing] commercial_fetch_emails_seconds={time.monotonic() - stage_t0:.2f}")
        email_ids = [int(r["id"]) for r in rows]

        stage_t0 = time.monotonic()
        _delete_existing_facts_for_emails(conn, email_ids)
        print(f"[timing] commercial_delete_existing_facts_seconds={time.monotonic() - stage_t0:.2f}")

        stage_t0 = time.monotonic()
        facts_summary = _build_email_facts(
            conn,
            rows=rows,
            run_id=run_id,
            internal_domains=internal_domains,
            vendor_domains=vendor_domains,
            existing_client_domains=existing_client_domains,
        )
        print(f"[timing] commercial_build_email_facts_seconds={time.monotonic() - stage_t0:.2f}")

        stage_t0 = time.monotonic()
        rollup_summary = _rebuild_rollups(conn, run_id=run_id)
        print(f"[timing] commercial_rebuild_rollups_seconds={time.monotonic() - stage_t0:.2f}")

        stage_t0 = time.monotonic()
        candidate_summary = _persist_candidates(conn, run_id=run_id)
        print(f"[timing] commercial_persist_candidates_seconds={time.monotonic() - stage_t0:.2f}")

        max_email_id = conn.execute("SELECT COALESCE(MAX(id),0) FROM emails").fetchone()[0]
        set_kv(conn, WATERMARK_KEY, str(int(max_email_id or 0)))
        set_kv(
            conn,
            SUMMARY_KEY,
            (
                f"emails_considered={len(rows)} signal_rows={facts_summary['signal_rows']} "
                f"org_candidates={candidate_summary['org_candidates']} "
                f"contact_candidates={candidate_summary['contact_candidates']} "
                f"opportunity_candidates={candidate_summary['opportunity_candidates']}"
            ),
        )

        print(f"DB: {settings.resolved_sqlite_path()}")
        print(f"Run id: {run_id}")
        print(f"Emails considered: {len(rows)}")
        print(
            "Signals summary: "
            f"signal_rows={facts_summary['signal_rows']} suppression_rows={facts_summary['suppression_rows']} "
            f"distinct_reason_codes={facts_summary['distinct_reason_codes']}"
        )
        print(
            "Rollups summary: "
            f"org_rollup_rows={rollup_summary['org_rollup_rows']} "
            f"contact_rollup_rows={rollup_summary['contact_rollup_rows']} "
            f"opportunity_fact_rows={rollup_summary['opportunity_fact_rows']}"
        )
        print(
            "Candidates summary: "
            f"org_candidates={candidate_summary['org_candidates']} "
            f"contact_candidates={candidate_summary['contact_candidates']} "
            f"opportunity_candidates={candidate_summary['opportunity_candidates']} "
            f"review_events_written={candidate_summary['review_events_written']}"
        )
        org_sup, contact_sup, opp_sup = fetch_candidate_suppressed_counts(conn)
        print(
            "Suppressed summary: "
            f"org={org_sup} contact={contact_sup} opportunity={opp_sup}"
        )
        return 0
    finally:
        finish_run(conn, run_id)
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

