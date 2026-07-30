"""Load ChileCompra + PR2 identity planes for procurement planning (read-only)."""

from __future__ import annotations

import sqlite3
from typing import Any

from origenlab_email_pipeline.commercial_procurement.constants import SOURCE_CHILECOMPRA
from origenlab_email_pipeline.commercial_procurement.link_routes import (
    AccountIndex,
    build_account_index,
)
from origenlab_email_pipeline.commercial_procurement.normalize import (
    buyer_fields_from_raw_and_lead,
    canonical_tender_key_from_raw,
    extract_status_fields,
    parse_raw_json,
)
from origenlab_email_pipeline.commercial_procurement.status import parse_tender_date

# Connection ids that must observe an active deferred read transaction.
_REQUIRE_TXN_CONN_IDS: set[int] = set()


def enable_require_active_read_transaction(conn: sqlite3.Connection) -> None:
    _REQUIRE_TXN_CONN_IDS.add(id(conn))


def disable_require_active_read_transaction(conn: sqlite3.Connection) -> None:
    _REQUIRE_TXN_CONN_IDS.discard(id(conn))


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def assert_active_read_transaction(conn: sqlite3.Connection) -> None:
    """When the builder pins a read txn, every loader must use that same txn."""
    if id(conn) in _REQUIRE_TXN_CONN_IDS and not conn.in_transaction:
        raise RuntimeError("procurement loader requires an active read transaction")


class SourceSchemaError(ValueError):
    """Required source or identity tables are missing."""


def require_pr2_identity_schema(conn: sqlite3.Connection) -> None:
    assert_active_read_transaction(conn)
    required = (
        "commercial_identity_account",
        "commercial_identity_account_domain",
        "commercial_identity_build_meta",
    )
    missing = [t for t in required if not _table_exists(conn, t)]
    if missing:
        raise SourceSchemaError(f"missing PR2 identity tables: {', '.join(missing)}")


def load_identity_fingerprint_meta(conn: sqlite3.Connection) -> dict[str, str | None]:
    require_pr2_identity_schema(conn)
    meta = {
        r["meta_key"]: r["meta_value"]
        for r in conn.execute("SELECT meta_key, meta_value FROM commercial_identity_build_meta")
    }
    return {
        "schema_version": meta.get("schema_version"),
        "identity_fingerprint": meta.get("identity_fingerprint"),
        "identity_fingerprint_algorithm_version": meta.get(
            "identity_fingerprint_algorithm_version"
        ),
        "run_context": meta.get("run_context"),
    }


def load_pr2_account_index(conn: sqlite3.Connection) -> AccountIndex:
    require_pr2_identity_schema(conn)
    accounts = [
        {
            "account_id": r["account_id"],
            "canonical_name_norm": (r["normalized_name"] or "").strip().lower(),
            "primary_domain_norm": (r["primary_domain"] or "").strip().lower(),
        }
        for r in conn.execute(
            "SELECT account_id, normalized_name, primary_domain FROM commercial_identity_account"
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
                "SELECT account_id, normalized_alias FROM commercial_identity_account_alias"
            )
        ]
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


def load_known_account_ids(conn: sqlite3.Connection) -> frozenset[str]:
    require_pr2_identity_schema(conn)
    return frozenset(
        r["account_id"]
        for r in conn.execute("SELECT account_id FROM commercial_identity_account")
    )


def load_pr3_immutability_sentinel(conn: sqlite3.Connection) -> dict[str, Any]:
    """Read-only PR3 presence counts used as an immutability sentinel (no mutation)."""
    assert_active_read_transaction(conn)
    if not _table_exists(conn, "commercial_opportunity"):
        return {"commercial_opportunity_count": 0, "present": False}
    n = int(conn.execute("SELECT COUNT(*) FROM commercial_opportunity").fetchone()[0])
    return {"commercial_opportunity_count": n, "present": True}


def _line_from_parts(
    *,
    source_record_id: str | None,
    raw_json: str | None,
    org_name: str | None,
    domain: str | None,
    email: str | None,
    region: str | None,
    first_seen_at: str | None,
    last_seen_at: str | None,
    join_status: str,
    lead_id: int | None = None,
) -> dict[str, Any]:
    sid = (source_record_id or "").strip() or None
    has_raw_source = join_status in {"matched", "raw_only"} and raw_json is not None
    has_lead_source = join_status in {"matched", "lead_only"}
    raw = parse_raw_json(raw_json) if raw_json is not None else None
    raw_json_malformed = bool(raw_json is not None and raw is None)
    tender_key, key_kind, verified = canonical_tender_key_from_raw(
        source_record_id=source_record_id,
        raw=raw,
    )
    status = extract_status_fields(raw)
    buyer = buyer_fields_from_raw_and_lead(
        org_name=org_name,
        domain=domain,
        email=email,
        raw=raw,
    )
    pub = status.get("publication_date")
    close = status.get("close_date")
    pub_d = parse_tender_date(pub)
    close_d = parse_tender_date(close)
    region_val = region or buyer.get("region_from_raw")
    return {
        "source_system": SOURCE_CHILECOMPRA,
        "lead_id": lead_id,
        "source_record_id": sid,
        "raw_lead_join_status": join_status,
        "has_raw_source": has_raw_source,
        "has_lead_source": has_lead_source,
        "raw_source_record_id": sid if has_raw_source else None,
        "lead_source_record_id": sid if has_lead_source else None,
        "raw_json_valid": (raw is not None) if raw_json is not None else False,
        "raw_json_malformed": raw_json_malformed,
        "tender_key": tender_key,
        "tender_key_kind": key_kind,
        "verified": verified,
        "buyer_display": buyer["buyer_display"],
        "buyer_name_norm": buyer["buyer_name_norm"],
        "buyer_domain": buyer["buyer_domain"],
        "email_norm": buyer["email_norm"],
        "email_domain": buyer["email_domain"],
        "weak_public_unit_name": buyer["weak_public_unit_name"],
        "region": region_val,
        "title": status.get("title"),
        "status_code": status["status_code"],
        "status_name": status["status_name"],
        "close_date": close,
        "publication_date": pub,
        "publication_date_parsed": pub_d.isoformat() if pub_d else "",
        "close_date_parsed": close_d.isoformat() if close_d else "",
        "first_seen_at": first_seen_at,
        "last_seen_at": last_seen_at,
    }


def load_chilecompra_source_lines(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Load all ChileCompra source outcomes (matched, lead-only, raw-only)."""
    assert_active_read_transaction(conn)
    if not _table_exists(conn, "lead_master") or not _table_exists(conn, "external_leads_raw"):
        raise SourceSchemaError("missing lead_master or external_leads_raw")

    lines: list[dict[str, Any]] = []
    for r in conn.execute(
        """
        SELECT lm.id AS lead_id,
               lm.source_record_id,
               lm.org_name,
               lm.domain,
               lm.domain_norm,
               lm.email,
               lm.email_norm,
               lm.region,
               lm.first_seen_at,
               lm.last_seen_at,
               er.raw_json
        FROM lead_master lm
        LEFT JOIN external_leads_raw er
          ON er.source_name = lm.source_name
         AND er.source_record_id = lm.source_record_id
        WHERE lower(lm.source_name) = ?
        """,
        (SOURCE_CHILECOMPRA,),
    ):
        join = "matched" if r["raw_json"] is not None else "lead_only"
        lines.append(
            _line_from_parts(
                source_record_id=r["source_record_id"],
                raw_json=r["raw_json"],
                org_name=r["org_name"],
                domain=r["domain_norm"] or r["domain"],
                email=r["email_norm"] or r["email"],
                region=r["region"],
                first_seen_at=r["first_seen_at"],
                last_seen_at=r["last_seen_at"],
                join_status=join,
                lead_id=int(r["lead_id"]) if r["lead_id"] is not None else None,
            )
        )

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
        lines.append(
            _line_from_parts(
                source_record_id=r["source_record_id"],
                raw_json=r["raw_json"],
                org_name=None,
                domain=None,
                email=None,
                region=None,
                first_seen_at=r["fetched_at"],
                last_seen_at=r["fetched_at"],
                join_status="raw_only",
            )
        )
    return lines


def build_source_pointer_registry(lines: list[dict[str, Any]]) -> frozenset[tuple[str, str]]:
    """Disposable registry of (source_table, source_record_id) that actually exist."""
    out: set[tuple[str, str]] = set()
    for line in lines:
        sid_raw = line.get("raw_source_record_id")
        sid_lead = line.get("lead_source_record_id")
        if line.get("has_raw_source") and sid_raw:
            out.add(("external_leads_raw", str(sid_raw)))
        if line.get("has_lead_source") and sid_lead:
            out.add(("lead_master", str(sid_lead)))
    return frozenset(out)


__all__ = [
    "SourceSchemaError",
    "assert_active_read_transaction",
    "build_source_pointer_registry",
    "disable_require_active_read_transaction",
    "enable_require_active_read_transaction",
    "load_chilecompra_source_lines",
    "load_identity_fingerprint_meta",
    "load_known_account_ids",
    "load_pr2_account_index",
    "load_pr3_immutability_sentinel",
    "require_pr2_identity_schema",
]
