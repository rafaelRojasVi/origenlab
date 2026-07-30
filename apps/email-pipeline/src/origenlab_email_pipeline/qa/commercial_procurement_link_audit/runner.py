"""Read-only production audit runner for PR4 procurement ↔ account linking."""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.qa.commercial_procurement_link_audit.constants import (
    AUDIT_NAME,
    AUDIT_VERSION,
    SOURCE_CHILECOMPRA,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.fingerprint import (
    procurement_source_fingerprint,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.link_routes import (
    build_account_index,
    classify_account_link_route,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.normalize import (
    buyer_fields_from_raw_and_lead,
    canonical_tender_key_from_raw,
    extract_status_fields,
    is_marketplace_domain,
    parse_raw_json,
    stable_token,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.redaction import (
    assert_no_leakage,
    redact_account_id,
    redact_buyer_name,
    scrub_row,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.readonly import (
    assert_no_write_connection,
    connect_readonly,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.schema_contract import (
    schema_contract_markdown,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.status import (
    classify_procurement_context,
)
from origenlab_email_pipeline.commercial_identity.normalize import (
    is_consumer_domain,
    is_internal_domain,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def _count(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                fieldnames.append(k)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            clean = scrub_row(r)
            w.writerow(clean)
            assert_no_leakage("|".join(str(v) for v in clean.values()))


def load_pr2_index(conn: sqlite3.Connection) -> Any:
    """Load persisted PR2 account/alias/domain planes (production column names)."""
    accounts = [
        {
            "account_id": r["account_id"],
            "canonical_name_norm": (r["normalized_name"] or "").strip().lower(),
            "primary_domain_norm": (r["primary_domain"] or "").strip().lower(),
        }
        for r in conn.execute(
            """
            SELECT account_id, normalized_name, primary_domain
            FROM commercial_identity_account
            """
        )
    ]
    aliases: list[dict[str, Any]] = []
    if _table_exists(conn, "commercial_identity_account_alias"):
        aliases = [
            {
                "account_id": r["account_id"],
                "alias_norm": (r["normalized_alias"] or "").strip().lower(),
            }
            for r in conn.execute(
                """
                SELECT account_id, normalized_alias
                FROM commercial_identity_account_alias
                """
            )
        ]
    domains: list[dict[str, Any]] = []
    if _table_exists(conn, "commercial_identity_account_domain"):
        domains = [
            {
                "account_id": r["account_id"],
                "domain_norm": (r["domain_norm"] or "").strip().lower(),
            }
            for r in conn.execute(
                """
                SELECT account_id, domain_norm
                FROM commercial_identity_account_domain
                WHERE COALESCE(is_institutional, 0) = 1
                """
            )
        ]
    return build_account_index(accounts=accounts, aliases=aliases, domains=domains)


def inventory_sources(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(
        *,
        table: str,
        ownership: str,
        row_count: int,
        distinct_tender_ids: int | str,
        distinct_buyers: int | str,
        structured_fields: str,
        date_status_coverage: str,
        domain_email_coverage: str,
        duplicate_line_behavior: str,
        grain: str,
        observation_kind: str,
        refresh_process: str,
        stable_source_key: str,
        suitable_canonical: str,
        notes: str = "",
    ) -> None:
        rows.append(
            {
                "table_or_artifact": table,
                "ownership": ownership,
                "row_count": row_count,
                "distinct_tender_identifiers": distinct_tender_ids,
                "distinct_buyer_names": distinct_buyers,
                "structured_fields": structured_fields,
                "date_status_coverage": date_status_coverage,
                "buyer_domain_email_coverage": domain_email_coverage,
                "duplicate_line_behavior": duplicate_line_behavior,
                "grain": grain,
                "observation_kind": observation_kind,
                "refresh_process": refresh_process,
                "stable_source_key": stable_source_key,
                "suitable_as_canonical_pr4_evidence": suitable_canonical,
                "notes": notes,
            }
        )

    chile_raw = _count(
        conn,
        "SELECT COUNT(*) FROM external_leads_raw WHERE lower(source_name)=?",
        (SOURCE_CHILECOMPRA,),
    )
    chile_lead = _count(
        conn,
        "SELECT COUNT(*) FROM lead_master WHERE lower(source_name)=?",
        (SOURCE_CHILECOMPRA,),
    )
    tender_buyer = _count(conn, "SELECT COUNT(*) FROM lead_master WHERE lead_type='tender_buyer'")
    ptr = (
        _count(conn, "SELECT COUNT(*) FROM lead_research_prospect WHERE classification='public_tender_review'")
        if _table_exists(conn, "lead_research_prospect")
        else 0
    )
    lam = _count(conn, "SELECT COUNT(*) FROM lead_account_master") if _table_exists(conn, "lead_account_master") else 0
    id_acct = (
        _count(conn, "SELECT COUNT(*) FROM commercial_identity_account")
        if _table_exists(conn, "commercial_identity_account")
        else 0
    )
    id_alias = (
        _count(conn, "SELECT COUNT(*) FROM commercial_identity_account_alias")
        if _table_exists(conn, "commercial_identity_account_alias")
        else 0
    )
    id_dom = (
        _count(conn, "SELECT COUNT(*) FROM commercial_identity_account_domain")
        if _table_exists(conn, "commercial_identity_account_domain")
        else 0
    )
    opp = (
        _count(conn, "SELECT COUNT(*) FROM commercial_opportunity")
        if _table_exists(conn, "commercial_opportunity")
        else 0
    )

    add(
        table="external_leads_raw (source_name=chilecompra)",
        ownership="leads_schema.py / fetch_chilecompra.py",
        row_count=chile_raw,
        distinct_tender_ids="see tender_duplicate_analysis (CodigoExterno in raw_json)",
        distinct_buyers="see buyer_identity_coverage",
        structured_fields="raw_json: Codigo, CodigoExterno, NombreOrganismo, FechaCierre, CodigoEstado, Link, …",
        date_status_coverage="in raw_json only — not first-class columns",
        domain_email_coverage="usually absent; marketplace URLs in Link must not become buyer domain",
        duplicate_line_behavior="one row per line/Codigo; many lines share CodigoExterno",
        grain="line-item (source_record_id often Codigo)",
        observation_kind="observed file ingest",
        refresh_process="scripts/leads/fetch_chilecompra.py (file)",
        stable_source_key="(source_name, source_record_id)",
        suitable_canonical="primary evidence plane for tender keys + buyer names (with coalesce)",
    )
    add(
        table="lead_master (source_name=chilecompra / lead_type=tender_buyer)",
        ownership="leads_normalize.normalize_chilecompra",
        row_count=chile_lead,
        distinct_tender_ids="source_record_id is often line-level; tender id only via raw",
        distinct_buyers=_count(
            conn,
            "SELECT COUNT(DISTINCT org_name_norm) FROM lead_master WHERE lower(source_name)=? AND org_name_norm IS NOT NULL AND org_name_norm!=''",
            (SOURCE_CHILECOMPRA,),
        ),
        structured_fields="org_name, domain, email, region, evidence_summary, status(workflow)",
        date_status_coverage="lead_master.status is workflow (nuevo…), NOT ChileCompra lifecycle",
        domain_email_coverage="domain_norm nonempty="
        + str(
            _count(
                conn,
                "SELECT COUNT(*) FROM lead_master WHERE lower(source_name)=? AND domain_norm IS NOT NULL AND domain_norm!=''",
                (SOURCE_CHILECOMPRA,),
            )
        ),
        duplicate_line_behavior="mirrors raw 1:1 for chilecompra",
        grain="line-item normalized",
        observation_kind="normalized from observed",
        refresh_process="normalize_leads.py",
        stable_source_key="(source_name, source_record_id)",
        suitable_canonical="buyer/org surface; must join raw_json for tender-level key + status/dates",
        notes=f"tender_buyer_rows={tender_buyer}",
    )
    add(
        table="lead_research_prospect (classification=public_tender_review)",
        ownership="lead_research_schema.py",
        row_count=ptr,
        distinct_tender_ids="none (no codigo_licitacion column)",
        distinct_buyers=_count(
            conn,
            "SELECT COUNT(DISTINCT organization_name) FROM lead_research_prospect WHERE classification='public_tender_review'",
        )
        if ptr
        else 0,
        structured_fields="organization_name, domain, buyer_type, classification, evidence_url",
        date_status_coverage="none structured for tender lifecycle",
        domain_email_coverage="prospect email/domain may exist",
        duplicate_line_behavior="research rows, not tender lines",
        grain="prospect/org",
        observation_kind="inferred/research presentation",
        refresh_process="DeepSearch / research automation",
        stable_source_key="prospect id",
        suitable_canonical="NO — presentation/research only; enrichment cue only",
    )
    add(
        table="lead_account_master (+ membership)",
        ownership="lead_accounts_schema.py",
        row_count=lam,
        distinct_tender_ids="n/a (account rollup)",
        distinct_buyers=lam,
        structured_fields="canonical_name, primary_domain, lead_count",
        date_status_coverage="n/a",
        domain_email_coverage="primary_domain",
        duplicate_line_behavior="many tender leads → one account",
        grain="public-buyer account rollup",
        observation_kind="inferred clustering over lead_master",
        refresh_process="lead account builders",
        stable_source_key="lead_account id",
        suitable_canonical="NO for PR4 identity — use PR2 commercial_identity_*; lead_account is separate lane",
    )
    add(
        table="commercial_identity_account / alias / domain",
        ownership="commercial_identity (PR2)",
        row_count=id_acct,
        distinct_tender_ids="n/a",
        distinct_buyers=id_acct,
        structured_fields="account_id, names, domains, aliases",
        date_status_coverage="n/a",
        domain_email_coverage=f"aliases={id_alias}; domains={id_dom}",
        duplicate_line_behavior="n/a",
        grain="canonical commercial account",
        observation_kind="rebuildable identity read model",
        refresh_process="build_commercial_identity_read_model.py --apply",
        stable_source_key="account_id",
        suitable_canonical="YES — link TARGET only (never mutated by PR4)",
    )
    add(
        table="commercial_opportunity_*",
        ownership="commercial_opportunity (PR3)",
        row_count=opp,
        distinct_tender_ids="n/a",
        distinct_buyers="n/a",
        structured_fields="opportunity stage read model",
        date_status_coverage="PR3 stages — independent of procurement context",
        domain_email_coverage="n/a",
        duplicate_line_behavior="n/a",
        grain="commercial opportunity",
        observation_kind="rebuildable stage read model",
        refresh_process="build_commercial_opportunity_read_model.py --apply",
        stable_source_key="opportunity_id",
        suitable_canonical="ISOLATION ONLY — PR4 must not create/advance stages",
    )
    add(
        table="equipment_first_* CSV / ChileCompra API publish",
        ownership="equipment_first_chilecompra_* + Postgres commercial.equipment_opportunity*",
        row_count=-1,
        distinct_tender_ids="codigo_licitacion in CSV/API artifacts",
        distinct_buyers="buyer field in queue CSV",
        structured_fields="codigo_licitacion, buyer, close_date, chilecompra_status(_code), validity_status",
        date_status_coverage="strong — API path carries status+dates",
        domain_email_coverage="usually none",
        duplicate_line_behavior="equipment filter may drop non-ICP lines; not full tender corpus",
        grain="tender (equipment-filtered)",
        observation_kind="observed API/CSV + presentation mirror",
        refresh_process="auto-refresh-chilecompra-equipment (does not write SQLite)",
        stable_source_key="opportunity_key / codigo_licitacion",
        suitable_canonical="SECONDARY for active equipment tenders; NOT full historical ChileCompra corpus in SQLite",
        notes="Filesystem/Postgres lineage only for this SQLite audit; inventory without mutating",
    )
    return rows


def run_procurement_link_audit(
    *,
    sqlite_path: Path,
    output_dir: Path,
    today: date | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    conn = connect_readonly(sqlite_path)
    try:
        assert_no_write_connection(conn)
        source_inventory = inventory_sources(conn)
        _write_csv(output_dir / "source_inventory.csv", source_inventory)

        # Load ChileCompra raw+lead joined by source_record_id
        lead_rows = list(
            conn.execute(
                """
                SELECT lm.id AS lead_id,
                       lm.source_record_id,
                       lm.org_name,
                       lm.org_name_norm,
                       lm.domain,
                       lm.domain_norm,
                       lm.email,
                       lm.email_norm,
                       lm.region,
                       lm.status AS lead_workflow_status,
                       lm.evidence_summary,
                       lm.first_seen_at,
                       lm.last_seen_at,
                       er.raw_json,
                       er.source_url
                FROM lead_master lm
                LEFT JOIN external_leads_raw er
                  ON er.source_name = lm.source_name
                 AND er.source_record_id = lm.source_record_id
                WHERE lower(lm.source_name) = ?
                """,
                (SOURCE_CHILECOMPRA,),
            )
        )

        tender_lines: list[dict[str, Any]] = []
        by_tender: dict[str, list[dict[str, Any]]] = defaultdict(list)
        raw_keys: list[dict[str, Any]] = []
        lead_keys: list[dict[str, Any]] = []

        missing_tender_id = 0
        missing_buyer = 0
        missing_status = 0
        missing_dates = 0
        consumer_email_n = 0
        marketplace_domain_n = 0
        internal_domain_n = 0
        email_present = 0
        domain_present = 0

        for r in lead_rows:
            raw = parse_raw_json(r["raw_json"])
            tender_key, key_kind = canonical_tender_key_from_raw(
                source_record_id=r["source_record_id"],
                raw=raw,
            )
            status = extract_status_fields(raw)
            buyer = buyer_fields_from_raw_and_lead(
                org_name=r["org_name"],
                domain=r["domain_norm"] or r["domain"],
                email=r["email_norm"] or r["email"],
                raw=raw,
            )
            ctx = classify_procurement_context(
                status_code=status["status_code"],
                status_name=status["status_name"],
                close_date=status["close_date"],
                publication_date=status["publication_date"],
                today=today,
            )
            if not tender_key:
                missing_tender_id += 1
            if not buyer["buyer_name_norm"] and not buyer["buyer_display"]:
                missing_buyer += 1
            if not status["status_code"] and not status["status_name"]:
                missing_status += 1
            if not status["close_date"] and not status["publication_date"]:
                missing_dates += 1
            if buyer["email_norm"]:
                email_present += 1
            if buyer["buyer_domain"]:
                domain_present += 1
            if buyer["email_domain"] and is_consumer_domain(buyer["email_domain"]):
                consumer_email_n += 1
            if buyer["email_domain"] and is_marketplace_domain(buyer["email_domain"]):
                marketplace_domain_n += 1
            if buyer["email_domain"] and is_internal_domain(buyer["email_domain"]):
                internal_domain_n += 1
            if is_marketplace_domain(buyer["buyer_domain"]):
                marketplace_domain_n += 1

            line = {
                "lead_id": r["lead_id"],
                "source_record_id": r["source_record_id"],
                "tender_key": tender_key,
                "tender_key_kind": key_kind,
                "buyer_display": buyer["buyer_display"],
                "buyer_name_norm": buyer["buyer_name_norm"],
                "buyer_domain": buyer["buyer_domain"],
                "email_norm": buyer["email_norm"],
                "email_domain": buyer["email_domain"],
                "weak_public_unit_name": buyer["weak_public_unit_name"],
                "region": r["region"],
                "status_code": status["status_code"],
                "status_name": status["status_name"],
                "close_date": status["close_date"],
                "publication_date": status["publication_date"],
                "procurement_context": ctx["procurement_context"],
                "context_reason_code": ctx["reason_code"],
                "lead_workflow_status": r["lead_workflow_status"],
            }
            tender_lines.append(line)
            if tender_key:
                by_tender[tender_key].append(line)
            raw_keys.append(
                {
                    "source_record_id": r["source_record_id"] or "",
                    "tender_key": tender_key or "",
                    "tender_key_kind": key_kind,
                }
            )
            lead_keys.append(
                {
                    "lead_id": int(r["lead_id"]),
                    "source_record_id": r["source_record_id"] or "",
                    "org_name_norm": r["org_name_norm"] or "",
                    "domain_norm": r["domain_norm"] or "",
                }
            )

        # Duplicate / coalesce analysis
        dup_rows = []
        coalesced = []
        multi_line = 0
        buyer_variants = 0
        for tender_key, lines in sorted(by_tender.items(), key=lambda kv: kv[0]):
            n = len(lines)
            if n > 1:
                multi_line += 1
            buyers = sorted({(x["buyer_name_norm"] or "") for x in lines if x["buyer_name_norm"]})
            if len(buyers) > 1:
                buyer_variants += 1
            # Representative line for linking: prefer institutional domain, else first.
            rep = sorted(
                lines,
                key=lambda x: (
                    0 if x["buyer_domain"] else 1,
                    0 if x["email_norm"] else 1,
                    x["source_record_id"] or "",
                ),
            )[0]
            coalesced.append(
                {
                    "tender_key": tender_key,
                    "tender_key_kind": rep["tender_key_kind"],
                    "line_item_count": n,
                    "buyer_name_norm": rep["buyer_name_norm"],
                    "buyer_display": rep["buyer_display"],
                    "buyer_domain": rep["buyer_domain"],
                    "email_norm": rep["email_norm"],
                    "email_domain": rep["email_domain"],
                    "weak_public_unit_name": rep["weak_public_unit_name"],
                    "status_code": rep["status_code"],
                    "status_name": rep["status_name"],
                    "close_date": rep["close_date"],
                    "publication_date": rep["publication_date"],
                    "procurement_context": rep["procurement_context"],
                    "context_reason_code": rep["context_reason_code"],
                    "distinct_buyer_norms": len(buyers),
                }
            )
            dup_rows.append(
                {
                    "tender_token": stable_token("tender", tender_key),
                    "tender_key_kind": rep["tender_key_kind"],
                    "line_item_count": n,
                    "distinct_buyer_norms": len(buyers),
                    "procurement_context": rep["procurement_context"],
                }
            )

        _write_csv(output_dir / "tender_duplicate_analysis.csv", dup_rows)

        # Overlap across planes
        ptr_n = 0
        if _table_exists(conn, "lead_research_prospect"):
            ptr_n = _count(
                conn,
                "SELECT COUNT(*) FROM lead_research_prospect WHERE classification='public_tender_review'",
            )
        overlap = [
            {
                "plane_a": "external_leads_raw chilecompra",
                "plane_b": "lead_master chilecompra",
                "relationship": "1:1 by (source_name, source_record_id)",
                "plane_a_n": len(lead_rows),
                "plane_b_n": len(lead_rows),
                "notes": "normalized from raw",
            },
            {
                "plane_a": "coalesced CodigoExterno tenders",
                "plane_b": "chilecompra line rows",
                "relationship": "1:N lines per tender",
                "plane_a_n": len(coalesced),
                "plane_b_n": len(tender_lines),
                "notes": f"multi_line_tenders={multi_line}; buyer_name_variants={buyer_variants}",
            },
            {
                "plane_a": "lead_research public_tender_review",
                "plane_b": "chilecompra lead_master",
                "relationship": "weak/org-name only; no tender id on research rows",
                "plane_a_n": ptr_n,
                "plane_b_n": len(lead_rows),
                "notes": "do not merge blindly",
            },
            {
                "plane_a": "equipment_first / API publish",
                "plane_b": "SQLite chilecompra leads",
                "relationship": "parallel lane; ICP-filtered; not full corpus in SQLite",
                "plane_a_n": "filesystem/postgres",
                "plane_b_n": len(lead_rows),
                "notes": "canonical SQLite evidence remains chilecompra raw+lead with CodigoExterno coalesce",
            },
        ]
        _write_csv(output_dir / "source_overlap.csv", overlap)

        buyer_coverage = [
            {
                "metric": "chilecompra_line_rows",
                "value": len(tender_lines),
            },
            {"metric": "coalesced_tenders", "value": len(coalesced)},
            {"metric": "multi_line_tenders", "value": multi_line},
            {"metric": "tenders_with_buyer_name_variants", "value": buyer_variants},
            {"metric": "missing_tender_id_lines", "value": missing_tender_id},
            {"metric": "missing_buyer_name_lines", "value": missing_buyer},
            {"metric": "missing_status_lines", "value": missing_status},
            {"metric": "missing_date_lines", "value": missing_dates},
            {"metric": "lines_with_email", "value": email_present},
            {"metric": "lines_with_institutional_buyer_domain", "value": domain_present},
            {"metric": "consumer_email_observations", "value": consumer_email_n},
            {"metric": "marketplace_domain_observations", "value": marketplace_domain_n},
            {"metric": "internal_domain_observations", "value": internal_domain_n},
            {
                "metric": "distinct_buyer_name_norm_coalesced",
                "value": len({c["buyer_name_norm"] for c in coalesced if c["buyer_name_norm"]}),
            },
        ]
        _write_csv(output_dir / "buyer_identity_coverage.csv", buyer_coverage)

        # Link routes against PR2
        index = load_pr2_index(conn)
        route_counter: Counter[str] = Counter()
        auto_link_n = 0
        linked_accounts: set[str] = set()
        route_examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
        conflict_rows: list[dict[str, Any]] = []
        queue_rows: list[dict[str, Any]] = []
        status_counter: Counter[str] = Counter()

        for sig in coalesced:
            status_counter[sig["procurement_context"]] += 1
            result = classify_account_link_route(
                index=index,
                buyer_name_norm=sig["buyer_name_norm"],
                buyer_domain=sig["buyer_domain"],
                email_domain=sig["email_domain"],
                email_norm=sig["email_norm"],
                weak_public_unit_name=bool(sig["weak_public_unit_name"]),
            )
            route_counter[result.route] += 1
            if result.auto_link_allowed and result.account_ids:
                auto_link_n += 1
                linked_accounts.update(result.account_ids)
            sample = {
                "tender_token": stable_token("tender", sig["tender_key"]),
                "buyer_token": redact_buyer_name(sig["buyer_display"] or sig["buyer_name_norm"]),
                "route": result.route,
                "confidence": result.confidence,
                "reason_code": result.reason_code,
                "auto_link_allowed": int(result.auto_link_allowed),
                "account_tokens": "|".join(redact_account_id(a) for a in result.account_ids),
                "notes": result.notes,
                "procurement_context": sig["procurement_context"],
            }
            if len(route_examples[result.route]) < 5:
                route_examples[result.route].append(sample)
            if not result.auto_link_allowed:
                conflict_rows.append(sample)
                # Enrichment queue candidate
                research = "domain"
                if result.reason_code.endswith("ambiguous") or "ambiguous" in result.reason_code:
                    research = "account_disambiguation"
                elif "contact" in result.reason_code or not sig["email_norm"]:
                    research = "contact"
                elif "status" in (sig["context_reason_code"] or "") or sig["procurement_context"] == "unknown":
                    research = "status_or_dates"
                elif not sig["tender_key"]:
                    research = "tender_id"
                priority = 0
                if sig["line_item_count"] > 1:
                    priority += 1
                if sig["buyer_domain"] or sig["email_norm"]:
                    priority += 1
                if sig["procurement_context"] in {"tender_active", "tender_watch"}:
                    priority += 2
                queue_rows.append(
                    {
                        **sample,
                        "recommended_research_field": research,
                        "priority": priority,
                        "line_item_count": sig["line_item_count"],
                    }
                )

        route_dist = []
        for route, n in sorted(route_counter.items(), key=lambda kv: (-kv[1], kv[0])):
            # false-positive risk heuristic for report
            risk = {
                "A_exact_institutional_domain": "low",
                "E_explicit_email_domain": "low",
                "C_exact_alias": "medium",
                "B_exact_canonical_name": "medium",
                "D_unique_compatible_name": "medium",
                "G_ambiguous_multiple_accounts": "n/a (blocked)",
                "H_consumer_internal_marketplace_refused": "n/a (blocked)",
                "I_name_domain_conflict": "n/a (blocked)",
                "F_no_match": "n/a",
            }.get(route, "review")
            auto = route in {
                "A_exact_institutional_domain",
                "E_explicit_email_domain",
                "C_exact_alias",
                "B_exact_canonical_name",
                "D_unique_compatible_name",
            }
            route_dist.append(
                {
                    "route": route,
                    "candidate_signal_count": n,
                    "unique_linked_accounts_sample_note": "see linked_account_count_total",
                    "false_positive_risk": risk,
                    "automatic_linking_allowed_policy": int(auto),
                    "example_count_redacted": len(route_examples.get(route, [])),
                }
            )
        route_dist.append(
            {
                "route": "_totals",
                "candidate_signal_count": len(coalesced),
                "unique_linked_accounts_sample_note": len(linked_accounts),
                "false_positive_risk": "",
                "automatic_linking_allowed_policy": auto_link_n,
                "example_count_redacted": "",
            }
        )
        _write_csv(output_dir / "account_link_route_distribution.csv", route_dist)

        # Flatten examples into conflicts file (bounded)
        conflict_out = []
        for route, samples in sorted(route_examples.items()):
            for s in samples:
                if s["auto_link_allowed"]:
                    continue
                conflict_out.append(s)
        conflict_out.extend(conflict_rows[:200])
        # de-dupe by tender_token+route
        seen: set[tuple[str, str]] = set()
        conflict_dedup = []
        for r in conflict_out:
            key = (r["tender_token"], r["route"])
            if key in seen:
                continue
            seen.add(key)
            conflict_dedup.append(r)
        _write_csv(output_dir / "account_link_conflicts_redacted.csv", conflict_dedup[:500])

        queue_rows.sort(key=lambda r: (-int(r["priority"]), r["route"], r["tender_token"]))
        _write_csv(output_dir / "enrichment_queue_candidates_redacted.csv", queue_rows[:1000])

        status_rows = [
            {"procurement_context": k, "coalesced_tender_count": v}
            for k, v in sorted(status_counter.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        _write_csv(output_dir / "procurement_status_coverage.csv", status_rows)

        coalesced_fp_rows = [
            {
                "tender_key": c["tender_key"],
                "tender_key_kind": c["tender_key_kind"],
                "line_item_count": c["line_item_count"],
                "buyer_name_norm": c["buyer_name_norm"] or "",
                "buyer_domain": c["buyer_domain"] or "",
                "procurement_context": c["procurement_context"],
            }
            for c in coalesced
        ]
        fp = procurement_source_fingerprint(
            chilecompra_raw_keys=raw_keys,
            chilecompra_lead_keys=lead_keys,
            coalesced_tender_keys=coalesced_fp_rows,
        )
        # Identity fingerprint from build_meta if present
        id_meta = {}
        if _table_exists(conn, "commercial_identity_build_meta"):
            id_meta = {
                r["meta_key"]: r["meta_value"]
                for r in conn.execute("SELECT meta_key, meta_value FROM commercial_identity_build_meta")
            }
        fp_out = {
            "audit_name": AUDIT_NAME,
            "audit_version": AUDIT_VERSION,
            "generated_at_utc": _utc_now(),
            "procurement_source_fingerprint": fp,
            "persisted_identity": {
                "schema_version": id_meta.get("schema_version"),
                "identity_fingerprint_algorithm_version": id_meta.get(
                    "identity_fingerprint_algorithm_version"
                ),
                "identity_fingerprint": id_meta.get("identity_fingerprint"),
                "run_context": id_meta.get("run_context"),
            },
            "pr3_isolation": {
                "commercial_opportunity_count": _count(conn, "SELECT COUNT(*) FROM commercial_opportunity")
                if _table_exists(conn, "commercial_opportunity")
                else 0,
                "note": "PR4 audit does not mutate or reinterpret PR3 stages",
            },
        }
        (output_dir / "source_fingerprints.json").write_text(
            json.dumps(fp_out, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        (output_dir / "proposed_schema.md").write_text(schema_contract_markdown() + "\n", encoding="utf-8")

        summary = {
            "audit_name": AUDIT_NAME,
            "audit_version": AUDIT_VERSION,
            "generated_at_utc": _utc_now(),
            "canonical_source_recommendation": {
                "primary": "external_leads_raw + lead_master chilecompra coalesced by CodigoExterno",
                "secondary_active_equipment": "ChileCompra API / equipment_first CSV → Postgres (not SQLite corpus)",
                "excluded_as_canonical": [
                    "lead_research_prospect public_tender_review",
                    "lead_account_* rollups",
                    "commercial_opportunity_*",
                    "dashboard/Postgres presentation mirrors as identity truth",
                ],
                "rationale": (
                    "SQLite holds the full chilecompra file corpus with raw_json tender keys; "
                    "equipment API path is ICP-filtered and lives outside SQLite; research rows lack tender IDs."
                ),
            },
            "metrics": {
                "chilecompra_line_rows": len(tender_lines),
                "coalesced_tenders": len(coalesced),
                "multi_line_tenders": multi_line,
                "buyer_name_variants_on_same_tender": buyer_variants,
                "route_distribution": dict(route_counter),
                "auto_link_allowed_signals": auto_link_n,
                "unique_accounts_auto_linkable": len(linked_accounts),
                "enrichment_queue_candidates": len(queue_rows),
                "procurement_context_distribution": dict(status_counter),
                "missing_tender_id_lines": missing_tender_id,
                "missing_buyer_lines": missing_buyer,
                "missing_status_lines": missing_status,
                "missing_date_lines": missing_dates,
                "consumer_email_observations": consumer_email_n,
                "marketplace_domain_observations": marketplace_domain_n,
            },
            "procurement_source_fingerprint": fp["fingerprint"],
            "procurement_source_fingerprint_algorithm": fp["algorithm"],
            "identity_fingerprint": id_meta.get("identity_fingerprint"),
            "safety": {
                "sqlite_mode": "ro+query_only",
                "mutations": False,
                "pr2_apply": False,
                "pr3_apply": False,
            },
        }
        (output_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        # Human audit report
        report = _render_audit_report(summary, source_inventory, route_dist, status_rows)
        assert_no_leakage(report)
        (output_dir / "audit_report.md").write_text(report, encoding="utf-8")
        return summary
    finally:
        conn.close()


def _render_audit_report(
    summary: dict[str, Any],
    source_inventory: list[dict[str, Any]],
    route_dist: list[dict[str, Any]],
    status_rows: list[dict[str, Any]],
) -> str:
    m = summary["metrics"]
    lines = [
        "# Commercial procurement ↔ account-link audit (PR4)",
        "",
        f"Generated: {summary['generated_at_utc']}",
        f"Audit: `{summary['audit_name']}` / `{summary['audit_version']}`",
        "",
        "## Canonical source recommendation",
        "",
        f"- **Primary:** {summary['canonical_source_recommendation']['primary']}",
        f"- **Secondary (active equipment):** {summary['canonical_source_recommendation']['secondary_active_equipment']}",
        f"- **Rationale:** {summary['canonical_source_recommendation']['rationale']}",
        "",
        "## Production metrics (dated checkpoint)",
        "",
        f"- ChileCompra line rows: **{m['chilecompra_line_rows']}**",
        f"- Coalesced tenders (CodigoExterno/key): **{m['coalesced_tenders']}**",
        f"- Multi-line tenders: **{m['multi_line_tenders']}**",
        f"- Same tender with buyer-name variants: **{m['buyer_name_variants_on_same_tender']}**",
        f"- Auto-link-allowed signals (policy): **{m['auto_link_allowed_signals']}**",
        f"- Unique auto-linkable accounts: **{m['unique_accounts_auto_linkable']}**",
        f"- Enrichment queue candidates: **{m['enrichment_queue_candidates']}**",
        "",
        "### Link-route distribution",
        "",
    ]
    for r in route_dist:
        if r["route"] == "_totals":
            continue
        lines.append(
            f"- `{r['route']}`: {r['candidate_signal_count']} "
            f"(auto_policy={r['automatic_linking_allowed_policy']}, risk={r['false_positive_risk']})"
        )
    lines.extend(["", "### Procurement-context distribution", ""])
    for s in status_rows:
        lines.append(f"- `{s['procurement_context']}`: {s['coalesced_tender_count']}")
    lines.extend(
        [
            "",
            "## Hard invariants (enforced in design)",
            "",
            "- Tender ≠ commercial relationship / PR3 stage.",
            "- Closed tender remains historical procurement.",
            "- PR2 accounts are link targets only (never mutated by PR4).",
            "- Consumer / internal / marketplace domains never establish institutional membership.",
            "- Build time never substitutes for missing tender dates.",
            "- Ambiguity → conflict/queue, never silent merge.",
            "",
            f"Source fingerprint (`{summary['procurement_source_fingerprint_algorithm']}`): "
            f"`{summary['procurement_source_fingerprint']}`",
            "",
            "See `proposed_schema.md` and committed "
            "`docs/audits/COMMERCIAL_PROCUREMENT_LINK_READ_MODEL_PR4.md`.",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


__all__ = ["run_procurement_link_audit"]
