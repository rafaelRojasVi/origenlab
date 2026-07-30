"""Read-only production audit runner for PR4 procurement ↔ account linking."""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.commercial_identity.normalize import (
    is_consumer_domain,
    is_internal_domain,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.coalesce import (
    coalesce_verified_tender_lines,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.constants import (
    AS_OF_INTERPRETATION,
    AS_OF_TIMEZONE,
    AUDIT_NAME,
    AUDIT_VERSION,
    RAW_KEY_INVENTORY_LABELS,
    RESOLVER_BUILD_CONTRACT_VERSION,
    SOURCE_CHILECOMPRA,
    TENDER_KEY_UNRESOLVED,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.fingerprint import (
    source_line_semantic_payload,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.link_routes import (
    build_account_index,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.normalize import (
    buyer_fields_from_raw_and_lead,
    canonical_tender_key_from_raw,
    extract_status_fields,
    inventory_raw_tender_key_presence,
    is_marketplace_domain,
    parse_raw_json,
    stable_token,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.output_redaction import (
    assert_no_leakage,
    redact_account_id,
    redact_buyer_name,
    scrub_row,
)
from origenlab_email_pipeline.commercial_procurement.planner import plan_procurement
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.schema_contract import (
    schema_contract_markdown,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.status import (
    parse_tender_date,
)
from origenlab_email_pipeline.qa.commercial_procurement_link_audit.sqlite_readonly import (
    assert_no_write_connection,
    connect_readonly,
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

    def add(**kwargs: Any) -> None:
        rows.append(kwargs)

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
        table_or_artifact="external_leads_raw (source_name=chilecompra)",
        ownership="leads_schema.py / fetch_chilecompra.py",
        row_count=chile_raw,
        distinct_tender_identifiers="see tender_key_kind_distribution (verified CodigoExterno etc.)",
        distinct_buyer_names="see buyer_identity_coverage",
        structured_fields="raw_json: Codigo, CodigoExterno, NombreOrganismo, FechaCierre, CodigoEstado, Link, …",
        date_status_coverage="in raw_json only — not first-class columns",
        buyer_domain_email_coverage="usually absent; marketplace URLs in Link must not become buyer domain",
        duplicate_line_behavior="one row per line/Codigo; many lines may share CodigoExterno",
        grain="line-item (source_record_id often Codigo)",
        observation_kind="observed file ingest",
        refresh_process="scripts/leads/fetch_chilecompra.py (file)",
        stable_source_key="(source_name, source_record_id)",
        suitable_as_canonical_pr4_evidence="primary evidence plane for verified tender keys + buyer names",
        notes="",
    )
    add(
        table_or_artifact="lead_master (source_name=chilecompra / lead_type=tender_buyer)",
        ownership="leads_normalize.normalize_chilecompra",
        row_count=chile_lead,
        distinct_tender_identifiers="source_record_id is often line-level; tender id only via raw",
        distinct_buyer_names=_count(
            conn,
            "SELECT COUNT(DISTINCT org_name_norm) FROM lead_master WHERE lower(source_name)=? AND org_name_norm IS NOT NULL AND org_name_norm!=''",
            (SOURCE_CHILECOMPRA,),
        ),
        structured_fields="org_name, domain, email, region, evidence_summary, status(workflow)",
        date_status_coverage="lead_master.status is workflow (nuevo…), NOT ChileCompra lifecycle",
        buyer_domain_email_coverage="domain_norm nonempty="
        + str(
            _count(
                conn,
                "SELECT COUNT(*) FROM lead_master WHERE lower(source_name)=? AND domain_norm IS NOT NULL AND domain_norm!=''",
                (SOURCE_CHILECOMPRA,),
            )
        ),
        duplicate_line_behavior="joined to raw by (source_name, source_record_id); not assumed 1:1",
        grain="line-item normalized",
        observation_kind="normalized from observed",
        refresh_process="normalize_leads.py",
        stable_source_key="(source_name, source_record_id)",
        suitable_as_canonical_pr4_evidence="buyer/org surface; must join raw_json for tender-level key + status/dates",
        notes=f"tender_buyer_rows={tender_buyer}",
    )
    add(
        table_or_artifact="lead_research_prospect (classification=public_tender_review)",
        ownership="lead_research_schema.py",
        row_count=ptr,
        distinct_tender_identifiers="none (no codigo_licitacion column)",
        distinct_buyer_names=_count(
            conn,
            "SELECT COUNT(DISTINCT organization_name) FROM lead_research_prospect WHERE classification='public_tender_review'",
        )
        if ptr
        else 0,
        structured_fields="organization_name, domain, buyer_type, classification, evidence_url",
        date_status_coverage="none structured for tender lifecycle",
        buyer_domain_email_coverage="prospect email/domain may exist",
        duplicate_line_behavior="research rows, not tender lines",
        grain="prospect/org",
        observation_kind="inferred/research presentation",
        refresh_process="DeepSearch / research automation",
        stable_source_key="prospect id",
        suitable_as_canonical_pr4_evidence="NO — presentation/research only; enrichment cue only",
        notes="",
    )
    add(
        table_or_artifact="lead_account_master (+ membership)",
        ownership="lead_accounts_schema.py",
        row_count=lam,
        distinct_tender_identifiers="n/a (account rollup)",
        distinct_buyer_names=lam,
        structured_fields="canonical_name, primary_domain, lead_count",
        date_status_coverage="n/a",
        buyer_domain_email_coverage="primary_domain",
        duplicate_line_behavior="many tender leads → one account",
        grain="public-buyer account rollup",
        observation_kind="inferred clustering over lead_master",
        refresh_process="lead account builders",
        stable_source_key="lead_account id",
        suitable_as_canonical_pr4_evidence="NO for PR4 identity — use PR2 commercial_identity_*; lead_account is separate lane",
        notes="",
    )
    add(
        table_or_artifact="commercial_identity_account / alias / domain",
        ownership="commercial_identity (PR2)",
        row_count=id_acct,
        distinct_tender_identifiers="n/a",
        distinct_buyer_names=id_acct,
        structured_fields="account_id, names, domains, aliases",
        date_status_coverage="n/a",
        buyer_domain_email_coverage=f"aliases={id_alias}; domains={id_dom}",
        duplicate_line_behavior="n/a",
        grain="canonical commercial account",
        observation_kind="rebuildable identity read model",
        refresh_process="build_commercial_identity_read_model.py --apply",
        stable_source_key="account_id",
        suitable_as_canonical_pr4_evidence="YES — link TARGET only (never mutated by PR4)",
        notes="",
    )
    add(
        table_or_artifact="commercial_opportunity_*",
        ownership="commercial_opportunity (PR3)",
        row_count=opp,
        distinct_tender_identifiers="n/a",
        distinct_buyer_names="n/a",
        structured_fields="opportunity stage read model",
        date_status_coverage="PR3 stages — independent of procurement context",
        buyer_domain_email_coverage="n/a",
        duplicate_line_behavior="n/a",
        grain="commercial opportunity",
        observation_kind="rebuildable stage read model",
        refresh_process="build_commercial_opportunity_read_model.py --apply",
        stable_source_key="opportunity_id",
        suitable_as_canonical_pr4_evidence="ISOLATION ONLY — PR4 must not create/advance stages",
        notes="",
    )
    add(
        table_or_artifact="equipment_first_* CSV / ChileCompra API publish",
        ownership="equipment_first_chilecompra_* + Postgres commercial.equipment_opportunity*",
        row_count=-1,
        distinct_tender_identifiers="codigo_licitacion in CSV/API artifacts",
        distinct_buyer_names="buyer field in queue CSV",
        structured_fields="codigo_licitacion, buyer, close_date, chilecompra_status(_code), validity_status",
        date_status_coverage="strong — API path carries status+dates",
        buyer_domain_email_coverage="usually none",
        duplicate_line_behavior="equipment filter may drop non-ICP lines; not full tender corpus",
        grain="tender (equipment-filtered)",
        observation_kind="observed API/CSV + presentation mirror",
        refresh_process="auto-refresh-chilecompra-equipment (does not write SQLite)",
        stable_source_key="opportunity_key / codigo_licitacion",
        suitable_as_canonical_pr4_evidence="SECONDARY for active equipment tenders; NOT full historical ChileCompra corpus in SQLite",
        notes="Filesystem/Postgres lineage only for this SQLite audit; inventory without mutating",
    )
    return rows


def run_procurement_link_audit(
    *,
    sqlite_path: Path,
    output_dir: Path,
    as_of_date: date,
) -> dict[str, Any]:
    if not isinstance(as_of_date, date):
        raise TypeError("as_of_date must be a datetime.date")
    output_dir.mkdir(parents=True, exist_ok=True)
    conn = connect_readonly(sqlite_path)
    try:
        assert_no_write_connection(conn)
        source_inventory = inventory_sources(conn)
        _write_csv(output_dir / "source_inventory.csv", source_inventory)

        raw_chilecompra_rows = _count(
            conn,
            "SELECT COUNT(*) FROM external_leads_raw WHERE lower(source_name)=?",
            (SOURCE_CHILECOMPRA,),
        )
        lead_chilecompra_rows = _count(
            conn,
            "SELECT COUNT(*) FROM lead_master WHERE lower(source_name)=?",
            (SOURCE_CHILECOMPRA,),
        )

        # Overlap without assuming 1:1
        matched_raw_lead_rows = _count(
            conn,
            """
            SELECT COUNT(*) FROM lead_master lm
            INNER JOIN external_leads_raw er
              ON er.source_name = lm.source_name
             AND er.source_record_id = lm.source_record_id
            WHERE lower(lm.source_name)=?
            """,
            (SOURCE_CHILECOMPRA,),
        )
        lead_only_rows = _count(
            conn,
            """
            SELECT COUNT(*) FROM lead_master lm
            WHERE lower(lm.source_name)=?
              AND NOT EXISTS (
                SELECT 1 FROM external_leads_raw er
                 WHERE er.source_name = lm.source_name
                   AND er.source_record_id = lm.source_record_id
              )
            """,
            (SOURCE_CHILECOMPRA,),
        )
        raw_only_rows = _count(
            conn,
            """
            SELECT COUNT(*) FROM external_leads_raw er
            WHERE lower(er.source_name)=?
              AND NOT EXISTS (
                SELECT 1 FROM lead_master lm
                 WHERE lm.source_name = er.source_name
                   AND lm.source_record_id = er.source_record_id
              )
            """,
            (SOURCE_CHILECOMPRA,),
        )
        duplicate_raw_source_keys = _count(
            conn,
            """
            SELECT COUNT(*) FROM (
              SELECT source_record_id FROM external_leads_raw
              WHERE lower(source_name)=?
              GROUP BY source_record_id HAVING COUNT(*) > 1
            )
            """,
            (SOURCE_CHILECOMPRA,),
        )
        duplicate_lead_source_keys = _count(
            conn,
            """
            SELECT COUNT(*) FROM (
              SELECT source_record_id FROM lead_master
              WHERE lower(source_name)=?
              GROUP BY source_record_id HAVING COUNT(*) > 1
            )
            """,
            (SOURCE_CHILECOMPRA,),
        )

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

        key_presence = Counter({k: 0 for k in RAW_KEY_INVENTORY_LABELS})
        tender_key_kind_distribution: Counter[str] = Counter()
        null_or_invalid_raw_json_rows = 0
        verified_lines: list[dict[str, Any]] = []
        unresolved_lines: list[dict[str, Any]] = []
        by_verified_tender: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

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
            if raw is None:
                null_or_invalid_raw_json_rows += 1
            for k, v in inventory_raw_tender_key_presence(
                raw, source_record_id=r["source_record_id"]
            ).items():
                key_presence[k] += v
            tender_key, key_kind, verified = canonical_tender_key_from_raw(
                source_record_id=r["source_record_id"],
                raw=raw,
            )
            tender_key_kind_distribution[key_kind] += 1
            status = extract_status_fields(raw)
            buyer = buyer_fields_from_raw_and_lead(
                org_name=r["org_name"],
                domain=r["domain_norm"] or r["domain"],
                email=r["email_norm"] or r["email"],
                raw=raw,
            )
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

            line = {
                "source_system": SOURCE_CHILECOMPRA,
                "lead_id": r["lead_id"],
                "source_record_id": r["source_record_id"],
                "raw_lead_join_status": "matched" if r["raw_json"] is not None else "lead_only",
                "raw_json_valid": raw is not None if r["raw_json"] is not None else False,
                "tender_key": tender_key,
                "tender_key_kind": key_kind,
                "verified": verified,
                "buyer_display": buyer["buyer_display"],
                "buyer_name_norm": buyer["buyer_name_norm"],
                "buyer_domain": buyer["buyer_domain"],
                "email_norm": buyer["email_norm"],
                "email_domain": buyer["email_domain"],
                "weak_public_unit_name": buyer["weak_public_unit_name"],
                "region": r["region"],
                "title": status.get("title"),
                "status_code": status["status_code"],
                "status_name": status["status_name"],
                "close_date": status["close_date"],
                "publication_date": status["publication_date"],
                "publication_date_parsed": (
                    parse_tender_date(status["publication_date"]).isoformat()
                    if status["publication_date"] and parse_tender_date(status["publication_date"])
                    else ""
                ),
                "close_date_parsed": (
                    parse_tender_date(status["close_date"]).isoformat()
                    if status["close_date"] and parse_tender_date(status["close_date"])
                    else ""
                ),
                "first_seen_at": r["first_seen_at"],
                "last_seen_at": r["last_seen_at"],
                "lead_workflow_status": r["lead_workflow_status"],
            }
            if verified and tender_key:
                verified_lines.append(line)
                by_verified_tender[(key_kind, tender_key)].append(line)
            else:
                unresolved_lines.append(line)

        # Raw-only rows (no lead) — included in source fingerprint components.
        raw_only_source_lines: list[dict[str, Any]] = []
        for r in conn.execute(
            """
            SELECT er.source_record_id, er.raw_json, er.fetched_at
            FROM external_leads_raw er
            WHERE lower(er.source_name)=?
              AND NOT EXISTS (
                SELECT 1 FROM lead_master lm
                 WHERE lm.source_name = er.source_name
                   AND lm.source_record_id = er.source_record_id
              )
            """,
            (SOURCE_CHILECOMPRA,),
        ):
            raw = parse_raw_json(r["raw_json"])
            tender_key, key_kind, verified = canonical_tender_key_from_raw(
                source_record_id=r["source_record_id"],
                raw=raw,
            )
            status = extract_status_fields(raw)
            buyer = buyer_fields_from_raw_and_lead(
                org_name=None, domain=None, email=None, raw=raw
            )
            raw_only_source_lines.append(
                {
                    "source_system": SOURCE_CHILECOMPRA,
                    "source_record_id": r["source_record_id"],
                    "raw_lead_join_status": "raw_only",
                    "raw_json_valid": raw is not None,
                    "tender_key": tender_key,
                    "tender_key_kind": key_kind,
                    "verified": verified,
                    "buyer_display": buyer["buyer_display"],
                    "buyer_name_norm": buyer["buyer_name_norm"],
                    "buyer_domain": buyer["buyer_domain"],
                    "email_norm": buyer["email_norm"],
                    "email_domain": buyer["email_domain"],
                    "weak_public_unit_name": buyer["weak_public_unit_name"],
                    "region": None,
                    "title": status.get("title"),
                    "status_code": status["status_code"],
                    "status_name": status["status_name"],
                    "close_date": status["close_date"],
                    "publication_date": status["publication_date"],
                    "first_seen_at": r["fetched_at"],
                    "last_seen_at": r["fetched_at"],
                }
            )

        all_source_lines_for_fp = verified_lines + unresolved_lines + raw_only_source_lines
        source_line_payloads = [source_line_semantic_payload(x) for x in all_source_lines_for_fp]

        coalesced_signals: list[dict[str, Any]] = []
        line_conflict_rows: list[dict[str, Any]] = []
        multi_line_verified = 0
        buyer_variants = 0
        for (key_kind, tender_key), lines in sorted(by_verified_tender.items(), key=lambda kv: kv[0]):
            agg = coalesce_verified_tender_lines(
                tender_key=tender_key,
                tender_key_kind=key_kind,
                lines=lines,
                as_of_date=as_of_date,
            )
            sig = agg["signal"]
            sig["source_system"] = SOURCE_CHILECOMPRA
            if sig["line_item_count"] > 1:
                multi_line_verified += 1
            buyers = {x["buyer_name_norm"] for x in lines if x.get("buyer_name_norm")}
            if len(buyers) > 1:
                buyer_variants += 1
            for c in agg["conflicts"]:
                line_conflict_rows.append(
                    {
                        "tender_token": stable_token("tender", tender_key),
                        "tender_key_kind": key_kind,
                        **{k: v for k, v in c.items() if k != "normalized_values" or True},
                    }
                )
            coalesced_signals.append(sig)

        verified_tender_level_key_rows = len(verified_lines)
        fallback_line_key_rows = sum(
            1 for x in unresolved_lines if x["tender_key_kind"] == TENDER_KEY_UNRESOLVED
        )
        unresolved_tender_key_rows = len(unresolved_lines)
        coalesced_verified_tenders = len(coalesced_signals)

        dup_rows = [
            {
                "tender_token": stable_token("tender", s["tender_key"]),
                "tender_key_kind": s["tender_key_kind"],
                "line_item_count": s["line_item_count"],
                "distinct_buyer_norms": len(
                    {
                        x["buyer_name_norm"]
                        for x in by_verified_tender[(s["tender_key_kind"], s["tender_key"])]
                        if x.get("buyer_name_norm")
                    }
                ),
                "procurement_context": s["procurement_context"],
                "material_conflict_n": len(s.get("line_conflicts") or []),
            }
            for s in coalesced_signals
        ]
        _write_csv(output_dir / "tender_duplicate_analysis.csv", dup_rows)

        key_inv_rows = [{"raw_json_key": k, "rows_with_key_present": key_presence[k]} for k in RAW_KEY_INVENTORY_LABELS]
        _write_csv(output_dir / "tender_key_inventory.csv", key_inv_rows)
        kind_dist_rows = [
            {"tender_key_kind": k, "row_count": v}
            for k, v in sorted(tender_key_kind_distribution.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        _write_csv(output_dir / "tender_key_kind_distribution.csv", kind_dist_rows)

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
                "relationship": "join on (source_name, source_record_id); not assumed 1:1",
                "plane_a_n": raw_chilecompra_rows,
                "plane_b_n": lead_chilecompra_rows,
                "matched_raw_lead_rows": matched_raw_lead_rows,
                "raw_only_rows": raw_only_rows,
                "lead_only_rows": lead_only_rows,
                "notes": "measure overlap; do not hardcode equality",
            },
            {
                "plane_a": "coalesced verified tenders",
                "plane_b": "verified tender-level key rows",
                "relationship": "1:N lines per verified tender key",
                "plane_a_n": coalesced_verified_tenders,
                "plane_b_n": verified_tender_level_key_rows,
                "notes": f"multi_line_verified={multi_line_verified}; buyer_name_variants={buyer_variants}",
            },
            {
                "plane_a": "lead_research public_tender_review",
                "plane_b": "chilecompra lead_master",
                "relationship": "weak/org-name only; no tender id on research rows",
                "plane_a_n": ptr_n,
                "plane_b_n": lead_chilecompra_rows,
                "notes": "do not merge blindly",
            },
        ]
        _write_csv(output_dir / "source_overlap.csv", overlap)

        grain_metrics = [
            {"metric": "raw_chilecompra_rows", "value": raw_chilecompra_rows},
            {"metric": "lead_chilecompra_rows", "value": lead_chilecompra_rows},
            {"metric": "matched_raw_lead_rows", "value": matched_raw_lead_rows},
            {"metric": "raw_only_rows", "value": raw_only_rows},
            {"metric": "lead_only_rows", "value": lead_only_rows},
            {"metric": "duplicate_raw_source_keys", "value": duplicate_raw_source_keys},
            {"metric": "duplicate_lead_source_keys", "value": duplicate_lead_source_keys},
            {"metric": "null_or_invalid_raw_json_rows", "value": null_or_invalid_raw_json_rows},
            {"metric": "verified_tender_level_key_rows", "value": verified_tender_level_key_rows},
            {"metric": "fallback_line_key_rows", "value": fallback_line_key_rows},
            {"metric": "unresolved_tender_key_rows", "value": unresolved_tender_key_rows},
            {"metric": "coalesced_verified_tenders", "value": coalesced_verified_tenders},
            {"metric": "multi_line_verified_tenders", "value": multi_line_verified},
            {"metric": "tenders_with_buyer_name_variants", "value": buyer_variants},
            {"metric": "missing_buyer_name_lines", "value": missing_buyer},
            {"metric": "missing_status_lines", "value": missing_status},
            {"metric": "missing_date_lines", "value": missing_dates},
            {"metric": "lines_with_email", "value": email_present},
            {"metric": "lines_with_institutional_buyer_domain", "value": domain_present},
            {"metric": "consumer_email_observations", "value": consumer_email_n},
            {"metric": "marketplace_domain_observations", "value": marketplace_domain_n},
            {"metric": "internal_domain_observations", "value": internal_domain_n},
        ]
        _write_csv(output_dir / "buyer_identity_coverage.csv", grain_metrics)

        index = load_pr2_index(conn)
        id_meta = {}
        if _table_exists(conn, "commercial_identity_build_meta"):
            id_meta = {
                r["meta_key"]: r["meta_value"]
                for r in conn.execute("SELECT meta_key, meta_value FROM commercial_identity_build_meta")
            }
        known_account_ids = frozenset(
            r["account_id"]
            for r in conn.execute("SELECT account_id FROM commercial_identity_account")
        ) if _table_exists(conn, "commercial_identity_account") else frozenset()

        # Single production resolver — audit reports derive from the immutable plan.
        plan = plan_procurement(
            source_lines=all_source_lines_for_fp,
            account_index=index,
            identity_fingerprint=id_meta.get("identity_fingerprint") or "",
            identity_fingerprint_algorithm_version=id_meta.get(
                "identity_fingerprint_algorithm_version"
            )
            or "unset",
            as_of_date=as_of_date,
            run_context="audit",
            known_account_ids=known_account_ids or None,
            generated_at_utc=_utc_now(),
        )
        fp = {
            "algorithm": plan.source_fingerprint and "procurement_source_fp_v1",
            "fingerprint": plan.source_fingerprint,
            "components": plan.source_fingerprint_components,
        }
        # Ensure algorithm key present for summary consumers
        fp["algorithm"] = "procurement_source_fp_v1"
        build_plan = {
            "algorithm": "procurement_build_plan_fp_v1",
            "fingerprint": plan.build_plan_fingerprint,
        }

        route_counter: Counter[str] = Counter(
            plan.metrics.get("route_distribution") or {}
        )
        resolution_status_counter: Counter[str] = Counter(
            plan.metrics.get("resolution_distribution") or {}
        )
        status_counter: Counter[str] = Counter(
            plan.metrics.get("procurement_context_distribution") or {}
        )
        auto_link_n = int(plan.metrics.get("linked_resolutions") or 0)
        linked_accounts: set[str] = {
            r.account_id for r in plan.resolutions if r.account_id
        }
        account_not_linked_total = int(plan.metrics.get("signal_count") or 0) - auto_link_n
        data_quality_conflict_total = len(plan.conflicts)
        enrichment_candidate_total = len(plan.enrichment_candidates)
        operator_queue_eligible_total = int(
            plan.metrics.get("operator_queue_eligible_count") or 0
        )

        signal_by_id = {s.procurement_id: s for s in plan.signals}
        route_examples: dict[str, list[dict[str, Any]]] = defaultdict(list)
        conflict_rows: list[dict[str, Any]] = []
        enrichment_rows: list[dict[str, Any]] = []

        for cres in plan.conflicts:
            if cres.subject_kind == "unresolved_source":
                conflict_rows.append(
                    {
                        "conflict_id": cres.conflict_id,
                        "subject_kind": cres.subject_kind,
                        "subject_key": cres.subject_key,
                        "source_system": cres.source_system,
                        "source_record_id_token": stable_token(
                            "src", cres.source_record_id or ""
                        ),
                        "tender_token": "",
                        "buyer_token": "",
                        "route": "unresolved_tender_key",
                        "resolution_status": "n/a",
                        "confidence": cres.confidence,
                        "reason_code": cres.reason_code,
                        "auto_link_allowed": 0,
                        "account_tokens": "",
                        "notes": "line/fallback key is not a verified tender-level identifier",
                        "procurement_context": "unknown",
                    }
                )

        for resolution in plan.resolutions:
            sig = signal_by_id[resolution.procurement_id]
            sample = {
                "tender_token": stable_token("tender", sig.canonical_tender_key),
                "buyer_token": redact_buyer_name(sig.buyer_name_raw or sig.buyer_name_norm),
                "route": resolution.link_route,
                "resolution_status": resolution.resolution_status,
                "confidence": resolution.confidence,
                "reason_code": resolution.reason_code,
                "auto_link_allowed": int(resolution.auto_link_allowed),
                "account_tokens": (
                    redact_account_id(resolution.account_id) if resolution.account_id else ""
                ),
                "candidate_account_tokens": "",
                "auxiliary_reason_codes": "",
                "notes": "",
                "procurement_context": sig.procurement_context,
            }
            if len(route_examples[resolution.link_route]) < 5:
                route_examples[resolution.link_route].append(sample)
            if resolution.resolution_status in {"ambiguous", "refused"}:
                conflict_rows.append(sample)

        for cand in plan.enrichment_candidates:
            sig = signal_by_id.get(cand.procurement_id) if cand.procurement_id else None
            enrichment_rows.append(
                {
                    "tender_token": stable_token(
                        "tender", sig.canonical_tender_key if sig else ""
                    ),
                    "buyer_token": redact_buyer_name(
                        (sig.buyer_name_raw if sig else None)
                        or cand.buyer_name_raw
                        or (sig.buyer_name_norm if sig else None)
                    ),
                    "route": "",
                    "resolution_status": "",
                    "confidence": cand.confidence,
                    "reason_code": cand.reason_code,
                    "auto_link_allowed": 0,
                    "account_tokens": "",
                    "recommended_research_field": cand.recommended_research_field,
                    "priority": cand.priority,
                    "operator_queue_eligible": cand.operator_queue_eligible,
                    "line_item_count": sig.line_item_count if sig else 0,
                    "procurement_context": sig.procurement_context if sig else "",
                }
            )

        route_dist = []
        for route, n in sorted(route_counter.items(), key=lambda kv: (-kv[1], kv[0])):
            risk = {
                "A_exact_institutional_domain": "low",
                "E_explicit_email_domain": "low",
                "C_exact_alias": "medium",
                "B_exact_canonical_name": "medium",
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
                "candidate_signal_count": len(coalesced_signals),
                "unique_linked_accounts_sample_note": len(linked_accounts),
                "false_positive_risk": "",
                "automatic_linking_allowed_policy": auto_link_n,
                "example_count_redacted": "",
            }
        )
        _write_csv(output_dir / "account_link_route_distribution.csv", route_dist)
        resolution_dist = [
            {"resolution_status": k, "signal_count": v}
            for k, v in sorted(resolution_status_counter.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        _write_csv(output_dir / "account_resolution_distribution.csv", resolution_dist)

        conflict_out: list[dict[str, Any]] = []
        for route, samples in sorted(route_examples.items()):
            for s in samples:
                if s["auto_link_allowed"]:
                    continue
                conflict_out.append(s)
        conflict_out.extend(conflict_rows[:300])
        conflict_out.extend(line_conflict_rows[:200])
        seen: set[tuple[str, str]] = set()
        conflict_dedup = []
        for r in conflict_out:
            key = (str(r.get("tender_token")), str(r.get("route") or r.get("reason_code")))
            if key in seen:
                continue
            seen.add(key)
            conflict_dedup.append(r)
        _write_csv(output_dir / "account_link_conflicts_redacted.csv", conflict_dedup[:500])

        enrichment_rows.sort(
            key=lambda r: (
                -int(r["operator_queue_eligible"]),
                -int(r["priority"]),
                r.get("route") or "",
                r.get("tender_token") or "",
            )
        )
        eligible_sample = [r for r in enrichment_rows if r["operator_queue_eligible"]][:500]
        other_sample = [r for r in enrichment_rows if not r["operator_queue_eligible"]][:200]
        _write_csv(
            output_dir / "enrichment_queue_candidates_redacted.csv",
            eligible_sample + other_sample,
        )

        status_rows = [
            {"procurement_context": k, "coalesced_verified_tender_count": v}
            for k, v in sorted(status_counter.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        _write_csv(output_dir / "procurement_status_coverage.csv", status_rows)
        fp_out = {
            "audit_name": AUDIT_NAME,
            "audit_version": AUDIT_VERSION,
            "generated_at_utc": _utc_now(),
            "as_of_date": as_of_date.isoformat(),
            "as_of_timezone": AS_OF_TIMEZONE,
            "as_of_interpretation": AS_OF_INTERPRETATION,
            "resolver_build_contract_version": RESOLVER_BUILD_CONTRACT_VERSION,
            "procurement_source_fingerprint": fp,
            "procurement_build_plan_fingerprint": build_plan,
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
            "as_of_date": as_of_date.isoformat(),
            "as_of_timezone": AS_OF_TIMEZONE,
            "as_of_interpretation": AS_OF_INTERPRETATION,
            "resolver_build_contract_version": RESOLVER_BUILD_CONTRACT_VERSION,
            "canonical_source_recommendation": {
                "primary": (
                    "external_leads_raw + lead_master chilecompra coalesced by verified "
                    "tender-level keys (CodigoExterno / CodigoLicitacion / numero_adquisicion)"
                ),
                "secondary_active_equipment": "ChileCompra API / equipment_first CSV → Postgres (not SQLite corpus)",
                "excluded_as_canonical": [
                    "line-level Codigo / Correlativo / source_record_id fallback as tender grain",
                    "lead_research_prospect public_tender_review",
                    "lead_account_* rollups",
                    "commercial_opportunity_*",
                    "dashboard/Postgres presentation mirrors as identity truth",
                ],
                "rationale": (
                    "Only documented tender-level identifiers define canonical procurement signals; "
                    "line fallbacks remain unresolved conflicts until proven tender-level."
                ),
            },
            "metrics": {
                "raw_chilecompra_rows": raw_chilecompra_rows,
                "lead_chilecompra_rows": lead_chilecompra_rows,
                "matched_raw_lead_rows": matched_raw_lead_rows,
                "raw_only_rows": raw_only_rows,
                "lead_only_rows": lead_only_rows,
                "duplicate_raw_source_keys": duplicate_raw_source_keys,
                "duplicate_lead_source_keys": duplicate_lead_source_keys,
                "null_or_invalid_raw_json_rows": null_or_invalid_raw_json_rows,
                "tender_key_kind_distribution": dict(tender_key_kind_distribution),
                "raw_key_presence_counts": dict(key_presence),
                "verified_tender_level_key_rows": verified_tender_level_key_rows,
                "fallback_line_key_rows": fallback_line_key_rows,
                "unresolved_tender_key_rows": unresolved_tender_key_rows,
                "coalesced_verified_tenders": coalesced_verified_tenders,
                "multi_line_verified_tenders": multi_line_verified,
                "buyer_name_variants_on_same_tender": buyer_variants,
                "route_distribution": dict(route_counter),
                "resolution_status_distribution": dict(resolution_status_counter),
                "auto_link_allowed_signals": auto_link_n,
                "unique_accounts_auto_linkable": len(linked_accounts),
                "account_not_linked_total": account_not_linked_total,
                "data_quality_conflict_total": data_quality_conflict_total,
                "enrichment_candidate_total": enrichment_candidate_total,
                "operator_queue_eligible_total": operator_queue_eligible_total,
                "source_fingerprint_line_count": len(source_line_payloads),
                "source_fingerprint_components": {
                    "all_source_lines_n": fp["components"]["all_source_lines"]["n"],
                    "verified_n": fp["components"]["verified_tender_key_lines"]["n"],
                    "unresolved_n": fp["components"]["unresolved_tender_key_lines"]["n"],
                    "verified_sha256": fp["components"]["verified_tender_key_lines"]["sha256"],
                    "unresolved_sha256": fp["components"]["unresolved_tender_key_lines"]["sha256"],
                },
                "procurement_context_distribution": dict(status_counter),
                "missing_buyer_lines": missing_buyer,
                "missing_status_lines": missing_status,
                "missing_date_lines": missing_dates,
                "consumer_email_observations": consumer_email_n,
                "marketplace_domain_observations": marketplace_domain_n,
            },
            "procurement_source_fingerprint": fp["fingerprint"],
            "procurement_source_fingerprint_algorithm": fp["algorithm"],
            "procurement_build_plan_fingerprint": build_plan["fingerprint"],
            "procurement_build_plan_fingerprint_algorithm": build_plan["algorithm"],
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
        report = _render_audit_report(summary, route_dist, status_rows)
        assert_no_leakage(report)
        (output_dir / "audit_report.md").write_text(report, encoding="utf-8")
        return summary
    finally:
        conn.close()


def _render_audit_report(
    summary: dict[str, Any],
    route_dist: list[dict[str, Any]],
    status_rows: list[dict[str, Any]],
) -> str:
    m = summary["metrics"]
    lines = [
        "# Commercial procurement ↔ account-link audit (PR4)",
        "",
        f"Generated: {summary['generated_at_utc']}",
        f"As-of date: `{summary['as_of_date']}` ({summary['as_of_timezone']})",
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
        f"- Raw chilecompra rows: **{m['raw_chilecompra_rows']}**",
        f"- Lead chilecompra rows: **{m['lead_chilecompra_rows']}**",
        f"- Matched raw↔lead: **{m['matched_raw_lead_rows']}** (raw_only={m['raw_only_rows']}, lead_only={m['lead_only_rows']})",
        f"- Verified tender-level key rows: **{m['verified_tender_level_key_rows']}**",
        f"- Unresolved / fallback key rows: **{m['unresolved_tender_key_rows']}**",
        f"- Coalesced *verified* tenders: **{m['coalesced_verified_tenders']}**",
        f"- Multi-line verified tenders: **{m['multi_line_verified_tenders']}**",
        f"- Auto-link-allowed signals: **{m['auto_link_allowed_signals']}**",
        f"- Account not linked: **{m['account_not_linked_total']}**",
        f"- Data-quality conflicts: **{m['data_quality_conflict_total']}**",
        f"- Enrichment candidates (multi-reason): **{m['enrichment_candidate_total']}**",
        f"- Operator-queue eligible: **{m['operator_queue_eligible_total']}**",
        "",
        "### Tender key kind distribution",
        "",
    ]
    for k, v in sorted(m["tender_key_kind_distribution"].items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"- `{k}`: {v}")
    lines.extend(["", "### Link-route distribution", ""])
    for r in route_dist:
        if r["route"] == "_totals":
            continue
        lines.append(
            f"- `{r['route']}`: {r['candidate_signal_count']} "
            f"(auto_policy={r['automatic_linking_allowed_policy']}, risk={r['false_positive_risk']})"
        )
    lines.extend(["", "### Procurement-context distribution (verified tenders)", ""])
    for s in status_rows:
        lines.append(f"- `{s['procurement_context']}`: {s['coalesced_verified_tender_count']}")
    lines.extend(
        [
            "",
            "## Hard invariants",
            "",
            "- Tender is not a commercial relationship or PR3 stage.",
            "- Only verified tender-level keys form canonical signals.",
            "- Refused consumer, internal, or marketplace domains do not block independent institutional evidence.",
            "- Build time never substitutes for missing tender dates; as_of_date is comparison-only.",
            "- Ambiguity becomes conflict or enrichment, never a silent merge.",
            "- Historical unmatched tenders are market history, not immediate operator tasks.",
            "",
            f"Source fingerprint (`{summary['procurement_source_fingerprint_algorithm']}`): "
            f"`{summary['procurement_source_fingerprint']}`",
            f"Build-plan fingerprint (`{summary['procurement_build_plan_fingerprint_algorithm']}`): "
            f"`{summary['procurement_build_plan_fingerprint']}`",
            "",
            "See `proposed_schema.md` and committed "
            "`docs/audits/COMMERCIAL_PROCUREMENT_LINK_READ_MODEL_PR4.md`.",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


__all__ = ["run_procurement_link_audit"]
