"""Mirror SQLite PR3 commercial opportunity lifecycle into Postgres.

ARCH-2A projection only.

SQLite PR3 remains source of truth. This module:
- opens SQLite read-only,
- validates the persisted PR3 production snapshot,
- validates graph integrity before any Postgres delete,
- reconstructs nullable display identity fields from mart source rows,
- guards against catastrophic projection collapse,
- atomically replaces the four Postgres projection tables.

It does not rebuild PR2/PR3 and does not mutate SQLite.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.commercial_identity.fingerprint import (
    identity_resolution_fingerprint,
)
from origenlab_email_pipeline.commercial_identity.ids import (
    stable_account_id_for_domain,
    stable_contact_id,
)
from origenlab_email_pipeline.commercial_identity.resolve import resolve_identity
from origenlab_email_pipeline.commercial_identity.sources import (
    load_source_identity_rows,
)
from origenlab_email_pipeline.commercial_opportunity.constants import (
    BUILD_CONTRACT,
    OPPORTUNITY_SOURCE_FINGERPRINT_ALGORITHM_VERSION,
    REQUIRED_IDENTITY_FINGERPRINT_ALGORITHM_VERSION,
    RUN_CONTEXT_PRODUCTION_APPLY,
    SCHEMA_VERSION,
)
from origenlab_email_pipeline.commercial_opportunity.source_fingerprint import (
    opportunity_source_fingerprint,
)
from origenlab_email_pipeline.commercial_opportunity.sources import (
    load_opportunity_sources,
)
from origenlab_email_pipeline.mart_core_postgres_migrate import connect_sqlite_readonly

try:
    import psycopg
    from psycopg.types.json import Json
except ImportError as exc:  # pragma: no cover
    psycopg = None  # type: ignore[misc, assignment]
    Json = None  # type: ignore[misc, assignment]
    _PSYCOPG_IMPORT_ERROR = exc
else:
    _PSYCOPG_IMPORT_ERROR = None


PG_TABLES: tuple[tuple[str, str], ...] = (
    ("commercial", "opportunity"),
    ("commercial", "opportunity_event"),
    ("commercial", "opportunity_evidence"),
    ("commercial", "opportunity_conflict"),
)

DELETE_ORDER: tuple[tuple[str, str], ...] = (
    ("commercial", "opportunity_conflict"),
    ("commercial", "opportunity_evidence"),
    ("commercial", "opportunity_event"),
    ("commercial", "opportunity"),
)

SQLITE_TABLES: tuple[str, ...] = (
    "commercial_opportunity",
    "commercial_opportunity_event",
    "commercial_opportunity_evidence",
    "commercial_opportunity_conflict",
    "commercial_opportunity_build_meta",
    "contact_master",
    "organization_master",
)

# Projection replacement is blocked if an existing non-empty Postgres opportunity
# population would suddenly fall below this fraction of its previous row count.
MIN_REPLACE_RATIO = 0.50


class CommercialOpportunityMirrorSafetyError(RuntimeError):
    """Fail-closed validation or replacement safety error."""


def _require_psycopg() -> None:
    if psycopg is None or Json is None:
        raise RuntimeError(
            f"psycopg is required (uv sync --group postgres). ({_PSYCOPG_IMPORT_ERROR})"
        )


def _sqlite_table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        LIMIT 1
        """,
        (table,),
    ).fetchone()
    return row is not None


def _require_sqlite_tables(conn: sqlite3.Connection) -> None:
    missing = [
        table for table in SQLITE_TABLES if not _sqlite_table_exists(conn, table)
    ]
    if missing:
        raise CommercialOpportunityMirrorSafetyError(
            "Required SQLite table(s) missing: " + ", ".join(missing)
        )


def _load_build_meta(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        """
        SELECT meta_key, meta_value
        FROM commercial_opportunity_build_meta
        """
    ).fetchall()
    return {str(row[0]): str(row[1]) for row in rows}


def validate_build_meta(meta: dict[str, str]) -> None:
    """Require a current, successfully-applied PR3 production snapshot."""

    required = (
        "schema_version",
        "build_contract",
        "built_at",
        "run_context",
        "identity_fingerprint",
        "identity_fingerprint_algorithm_version",
        "identity_fingerprint_match_status",
        "opportunity_source_fingerprint",
        "opportunity_source_fingerprint_algorithm_version",
    )
    missing = [key for key in required if not meta.get(key)]
    if missing:
        raise CommercialOpportunityMirrorSafetyError(
            "PR3 build metadata missing: " + ", ".join(missing)
        )

    if meta["schema_version"] != SCHEMA_VERSION:
        raise CommercialOpportunityMirrorSafetyError(
            "PR3 schema_version mismatch: "
            f"persisted={meta['schema_version']!r} expected={SCHEMA_VERSION!r}"
        )

    if meta["build_contract"] != BUILD_CONTRACT:
        raise CommercialOpportunityMirrorSafetyError(
            "PR3 build_contract mismatch: "
            f"persisted={meta['build_contract']!r} expected={BUILD_CONTRACT!r}"
        )

    if meta["run_context"] != RUN_CONTEXT_PRODUCTION_APPLY:
        raise CommercialOpportunityMirrorSafetyError(
            "Refusing mirror from non-production-apply PR3 snapshot: "
            f"run_context={meta['run_context']!r}"
        )

    if meta["identity_fingerprint_match_status"] != "matched":
        raise CommercialOpportunityMirrorSafetyError(
            "Refusing mirror from stale/mismatched PR3 identity snapshot: "
            f"identity_fingerprint_match_status="
            f"{meta['identity_fingerprint_match_status']!r}"
        )

    if (
        meta["identity_fingerprint_algorithm_version"]
        != REQUIRED_IDENTITY_FINGERPRINT_ALGORITHM_VERSION
    ):
        raise CommercialOpportunityMirrorSafetyError(
            "PR3 identity fingerprint algorithm mismatch: "
            f"persisted={meta['identity_fingerprint_algorithm_version']!r} "
            f"expected={REQUIRED_IDENTITY_FINGERPRINT_ALGORITHM_VERSION!r}"
        )

    if (
        meta["opportunity_source_fingerprint_algorithm_version"]
        != OPPORTUNITY_SOURCE_FINGERPRINT_ALGORITHM_VERSION
    ):
        raise CommercialOpportunityMirrorSafetyError(
            "PR3 source fingerprint algorithm mismatch: "
            f"persisted={meta['opportunity_source_fingerprint_algorithm_version']!r} "
            f"expected={OPPORTUNITY_SOURCE_FINGERPRINT_ALGORITHM_VERSION!r}"
        )


def validate_source_freshness(
    conn: sqlite3.Connection,
    meta: dict[str, str],
) -> dict[str, str]:
    """Prove persisted PR3 still matches its live SQLite source snapshot."""

    identity_rows = load_source_identity_rows(conn)
    sources = load_opportunity_sources(conn)

    live_identity = resolve_identity(identity_rows)
    live_identity_fingerprint = identity_resolution_fingerprint(live_identity)

    live_source_fingerprint = opportunity_source_fingerprint(
        deals=sources["deals"],
        events=sources["events"],
        documents=sources["documents"],
        payments=sources["payments"],
        signals=sources["signals"],
        contact_master=sources["contact_master"],
    )

    if live_identity_fingerprint != meta["identity_fingerprint"]:
        raise CommercialOpportunityMirrorSafetyError(
            "Refusing stale PR3 mirror: live identity fingerprint no longer "
            "matches persisted PR3 identity fingerprint"
        )

    if live_source_fingerprint != meta["opportunity_source_fingerprint"]:
        raise CommercialOpportunityMirrorSafetyError(
            "Refusing stale PR3 mirror: live opportunity source fingerprint no "
            "longer matches persisted PR3 source fingerprint"
        )

    return {
        "identity_fingerprint": live_identity_fingerprint,
        "opportunity_source_fingerprint": live_source_fingerprint,
    }


def _json_value(raw: Any, *, field_name: str) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list, bool, int, float)):
        return raw
    try:
        return json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CommercialOpportunityMirrorSafetyError(
            f"Malformed JSON in {field_name}"
        ) from exc


def _load_rows(
    conn: sqlite3.Connection,
    query: str,
) -> list[dict[str, Any]]:
    cur = conn.execute(query)
    columns = [str(desc[0]) for desc in cur.description or ()]
    return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


def _build_contact_display_index(conn: sqlite3.Connection) -> dict[str, str]:
    out: dict[str, str] = {}

    for (raw_email,) in conn.execute(
        """
        SELECT email
        FROM contact_master
        WHERE email IS NOT NULL
          AND TRIM(email) <> ''
        """
    ):
        email = str(raw_email).strip().lower()
        contact_id = stable_contact_id(email)

        existing = out.get(contact_id)
        if existing is not None and existing != email:
            raise CommercialOpportunityMirrorSafetyError(
                f"Contact stable-ID collision for {contact_id}"
            )

        out[contact_id] = email

    return out


def _build_account_display_index(conn: sqlite3.Connection) -> dict[str, str]:
    out: dict[str, str] = {}

    for (raw_domain,) in conn.execute(
        """
        SELECT domain
        FROM organization_master
        WHERE domain IS NOT NULL
          AND TRIM(domain) <> ''
        """
    ):
        domain = str(raw_domain).strip().lower()
        account_id = stable_account_id_for_domain(domain)

        existing = out.get(account_id)
        if existing is not None and existing != domain:
            raise CommercialOpportunityMirrorSafetyError(
                f"Account stable-ID collision for {account_id}"
            )

        out[account_id] = domain

    return out


def load_commercial_opportunity_mirror_payload(
    sqlite_path: Path,
) -> dict[str, Any]:
    """Load and validate the complete SQLite PR3 projection payload read-only."""

    conn = connect_sqlite_readonly(sqlite_path)
    conn.row_factory = sqlite3.Row
    try:
        # Hold one read transaction so freshness validation and projection loading
        # observe the same SQLite snapshot.
        conn.execute("BEGIN")

        _require_sqlite_tables(conn)

        meta = _load_build_meta(conn)
        validate_build_meta(meta)
        freshness = validate_source_freshness(conn, meta)

        contact_display_by_id = _build_contact_display_index(conn)
        account_display_by_id = _build_account_display_index(conn)

        opportunities = _load_rows(
            conn,
            """
            SELECT
              opportunity_id,
              record_kind,
              account_id,
              primary_contact_id,
              source_kind,
              source_key,
              deal_key,
              canonical_stage,
              source_stage,
              stage_reason_code,
              stage_confidence,
              stage_is_current,
              stage_is_terminal,
              stage_evidence_at,
              stage_evidence_id,
              first_activity_at,
              last_activity_at,
              identity_link_status,
              review_status
            FROM commercial_opportunity
            ORDER BY opportunity_id
            """,
        )

        for row in opportunities:
            contact_id = row.get("primary_contact_id")
            account_id = row.get("account_id")

            row["contact_display_email"] = (
                contact_display_by_id.get(str(contact_id))
                if contact_id is not None
                else None
            )
            row["account_display_domain"] = (
                account_display_by_id.get(str(account_id))
                if account_id is not None
                else None
            )

            row["stage_is_current"] = bool(row["stage_is_current"])
            row["stage_is_terminal"] = bool(row["stage_is_terminal"])

        events = _load_rows(
            conn,
            """
            SELECT
              event_id,
              opportunity_id,
              canonical_event_type,
              source_event_type,
              event_at,
              source_table,
              source_record_id,
              source_email_id,
              source_attachment_id,
              confidence,
              operator_confirmed,
              detail_json
            FROM commercial_opportunity_event
            ORDER BY event_id
            """,
        )
        for row in events:
            row["operator_confirmed"] = bool(row["operator_confirmed"])
            row["detail_json"] = _json_value(
                row["detail_json"],
                field_name="commercial_opportunity_event.detail_json",
            )

        evidence = _load_rows(
            conn,
            """
            SELECT
              evidence_id,
              opportunity_id,
              subject_kind,
              source_table,
              source_record_id,
              evidence_type,
              evidence_at,
              confidence,
              reason_code,
              source_email_id,
              source_attachment_id,
              detail_json
            FROM commercial_opportunity_evidence
            ORDER BY evidence_id
            """,
        )
        for row in evidence:
            row["detail_json"] = _json_value(
                row["detail_json"],
                field_name="commercial_opportunity_evidence.detail_json",
            )

        conflicts = _load_rows(
            conn,
            """
            SELECT
              conflict_id,
              opportunity_id,
              conflict_type,
              reason_code,
              subject_keys_json,
              evidence_pointers_json,
              review_status,
              detail_json
            FROM commercial_opportunity_conflict
            ORDER BY conflict_id
            """,
        )
        for row in conflicts:
            row["subject_keys_json"] = _json_value(
                row["subject_keys_json"],
                field_name="commercial_opportunity_conflict.subject_keys_json",
            )
            row["evidence_pointers_json"] = _json_value(
                row["evidence_pointers_json"],
                field_name="commercial_opportunity_conflict.evidence_pointers_json",
            )
            row["detail_json"] = _json_value(
                row["detail_json"],
                field_name="commercial_opportunity_conflict.detail_json",
            )

        payload = {
            "meta": meta,
            "freshness": freshness,
            "opportunities": opportunities,
            "events": events,
            "evidence": evidence,
            "conflicts": conflicts,
        }

        validate_payload_integrity(payload)
        return payload
    finally:
        conn.close()


def _require_unique_ids(
    rows: list[dict[str, Any]],
    *,
    id_field: str,
    label: str,
) -> set[str]:
    ids = [str(row[id_field]) for row in rows]

    if len(ids) != len(set(ids)):
        raise CommercialOpportunityMirrorSafetyError(
            f"Duplicate deterministic IDs in {label}"
        )

    return set(ids)


def validate_payload_integrity(payload: dict[str, Any]) -> None:
    opportunities = payload["opportunities"]
    events = payload["events"]
    evidence = payload["evidence"]
    conflicts = payload["conflicts"]

    opportunity_ids = _require_unique_ids(
        opportunities,
        id_field="opportunity_id",
        label="commercial_opportunity",
    )
    _require_unique_ids(
        events,
        id_field="event_id",
        label="commercial_opportunity_event",
    )
    evidence_ids = _require_unique_ids(
        evidence,
        id_field="evidence_id",
        label="commercial_opportunity_evidence",
    )
    _require_unique_ids(
        conflicts,
        id_field="conflict_id",
        label="commercial_opportunity_conflict",
    )

    orphan_events = [
        row["event_id"]
        for row in events
        if str(row["opportunity_id"]) not in opportunity_ids
    ]
    if orphan_events:
        raise CommercialOpportunityMirrorSafetyError(
            f"PR3 payload contains {len(orphan_events)} orphan event row(s)"
        )

    orphan_evidence = [
        row["evidence_id"]
        for row in evidence
        if str(row["opportunity_id"]) not in opportunity_ids
    ]
    if orphan_evidence:
        raise CommercialOpportunityMirrorSafetyError(
            f"PR3 payload contains {len(orphan_evidence)} orphan evidence row(s)"
        )

    orphan_conflicts = [
        row["conflict_id"]
        for row in conflicts
        if row["opportunity_id"] is not None
        and str(row["opportunity_id"]) not in opportunity_ids
    ]
    if orphan_conflicts:
        raise CommercialOpportunityMirrorSafetyError(
            f"PR3 payload contains {len(orphan_conflicts)} orphan conflict row(s)"
        )

    bad_stage_evidence = [
        row["opportunity_id"]
        for row in opportunities
        if row["stage_evidence_id"] is not None
        and str(row["stage_evidence_id"]) not in evidence_ids
    ]
    if bad_stage_evidence:
        raise CommercialOpportunityMirrorSafetyError(
            "PR3 payload contains opportunity stage_evidence_id values "
            "that do not resolve to evidence"
        )


def payload_counts(payload: dict[str, Any]) -> dict[str, int]:
    return {
        "opportunity": len(payload["opportunities"]),
        "opportunity_event": len(payload["events"]),
        "opportunity_evidence": len(payload["evidence"]),
        "opportunity_conflict": len(payload["conflicts"]),
    }


def enrichment_counts(payload: dict[str, Any]) -> dict[str, int]:
    opportunities = payload["opportunities"]

    return {
        "contact_display_email_resolved": sum(
            1 for row in opportunities if row["contact_display_email"] is not None
        ),
        "account_display_domain_resolved": sum(
            1 for row in opportunities if row["account_display_domain"] is not None
        ),
    }


def pg_commercial_opportunity_tables_exist(cur: Any) -> bool:
    for schema, table in PG_TABLES:
        cur.execute(
            """
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = %s
              AND table_name = %s
            LIMIT 1
            """,
            (schema, table),
        )
        if cur.fetchone() is None:
            return False

    return True


def postgres_commercial_opportunity_counts(cur: Any) -> dict[str, int]:
    out: dict[str, int] = {}

    for _, table in PG_TABLES:
        cur.execute(f"SELECT COUNT(*) FROM commercial.{table}")
        row = cur.fetchone()
        out[table] = int(row[0]) if row else 0

    return out


def assert_safe_replace(
    source_counts: dict[str, int],
    existing_counts: dict[str, int],
    *,
    min_replace_ratio: float = MIN_REPLACE_RATIO,
) -> None:
    """Block catastrophic replacement before any DELETE occurs."""

    source = int(source_counts.get("opportunity") or 0)
    existing = int(existing_counts.get("opportunity") or 0)

    # Empty PR3 is unsafe even for a first-ever projection. A zero-row
    # projection can reflect a stale/incomplete source build and must require
    # an explicit future break-glass path rather than publishing silently.
    if source == 0:
        raise CommercialOpportunityMirrorSafetyError(
            "Refusing commercial opportunity mirror replacement: "
            f"source opportunities=0 while Postgres currently has {existing}"
        )

    # First-ever non-empty projection has no previous population to compare
    # against for collapse detection.
    if existing == 0:
        return

    ratio = source / existing
    if ratio < min_replace_ratio:
        raise CommercialOpportunityMirrorSafetyError(
            "Refusing commercial opportunity mirror replacement: "
            f"source opportunities={source}, postgres opportunities={existing}, "
            f"ratio={ratio:.4f}, required>={min_replace_ratio:.4f}"
        )


def _insert_payload(
    cur: Any,
    payload: dict[str, Any],
    *,
    synced_at: datetime,
) -> None:
    assert Json is not None

    for row in payload["opportunities"]:
        cur.execute(
            """
            INSERT INTO commercial.opportunity (
              opportunity_id,
              record_kind,
              account_id,
              primary_contact_id,
              contact_display_email,
              account_display_domain,
              source_kind,
              source_key,
              deal_key,
              canonical_stage,
              source_stage,
              stage_reason_code,
              stage_confidence,
              stage_is_current,
              stage_is_terminal,
              stage_evidence_at,
              stage_evidence_id,
              first_activity_at,
              last_activity_at,
              identity_link_status,
              review_status,
              synced_at
            ) VALUES (
              %(opportunity_id)s,
              %(record_kind)s,
              %(account_id)s,
              %(primary_contact_id)s,
              %(contact_display_email)s,
              %(account_display_domain)s,
              %(source_kind)s,
              %(source_key)s,
              %(deal_key)s,
              %(canonical_stage)s,
              %(source_stage)s,
              %(stage_reason_code)s,
              %(stage_confidence)s,
              %(stage_is_current)s,
              %(stage_is_terminal)s,
              %(stage_evidence_at)s,
              %(stage_evidence_id)s,
              %(first_activity_at)s,
              %(last_activity_at)s,
              %(identity_link_status)s,
              %(review_status)s,
              %(synced_at)s
            )
            """,
            {**row, "synced_at": synced_at},
        )

    for row in payload["events"]:
        cur.execute(
            """
            INSERT INTO commercial.opportunity_event (
              event_id,
              opportunity_id,
              canonical_event_type,
              source_event_type,
              event_at,
              source_table,
              source_record_id,
              source_email_id,
              source_attachment_id,
              confidence,
              operator_confirmed,
              detail_json,
              synced_at
            ) VALUES (
              %(event_id)s,
              %(opportunity_id)s,
              %(canonical_event_type)s,
              %(source_event_type)s,
              %(event_at)s,
              %(source_table)s,
              %(source_record_id)s,
              %(source_email_id)s,
              %(source_attachment_id)s,
              %(confidence)s,
              %(operator_confirmed)s,
              %(detail_json)s,
              %(synced_at)s
            )
            """,
            {
                **row,
                "detail_json": (
                    Json(row["detail_json"]) if row["detail_json"] is not None else None
                ),
                "synced_at": synced_at,
            },
        )

    for row in payload["evidence"]:
        cur.execute(
            """
            INSERT INTO commercial.opportunity_evidence (
              evidence_id,
              opportunity_id,
              subject_kind,
              source_table,
              source_record_id,
              evidence_type,
              evidence_at,
              confidence,
              reason_code,
              source_email_id,
              source_attachment_id,
              detail_json,
              synced_at
            ) VALUES (
              %(evidence_id)s,
              %(opportunity_id)s,
              %(subject_kind)s,
              %(source_table)s,
              %(source_record_id)s,
              %(evidence_type)s,
              %(evidence_at)s,
              %(confidence)s,
              %(reason_code)s,
              %(source_email_id)s,
              %(source_attachment_id)s,
              %(detail_json)s,
              %(synced_at)s
            )
            """,
            {
                **row,
                "detail_json": (
                    Json(row["detail_json"]) if row["detail_json"] is not None else None
                ),
                "synced_at": synced_at,
            },
        )

    for row in payload["conflicts"]:
        cur.execute(
            """
            INSERT INTO commercial.opportunity_conflict (
              conflict_id,
              opportunity_id,
              conflict_type,
              reason_code,
              subject_keys_json,
              evidence_pointers_json,
              review_status,
              detail_json,
              synced_at
            ) VALUES (
              %(conflict_id)s,
              %(opportunity_id)s,
              %(conflict_type)s,
              %(reason_code)s,
              %(subject_keys_json)s,
              %(evidence_pointers_json)s,
              %(review_status)s,
              %(detail_json)s,
              %(synced_at)s
            )
            """,
            {
                **row,
                "subject_keys_json": Json(row["subject_keys_json"]),
                "evidence_pointers_json": Json(row["evidence_pointers_json"]),
                "detail_json": (
                    Json(row["detail_json"]) if row["detail_json"] is not None else None
                ),
                "synced_at": synced_at,
            },
        )


def sync_commercial_opportunity_postgres_mirror(
    pg_url: str,
    sqlite_path: Path,
    *,
    dry_run: bool = False,
    min_replace_ratio: float = MIN_REPLACE_RATIO,
) -> dict[str, Any]:
    """Validate then atomically replace the ARCH-2A Postgres projection."""

    payload = load_commercial_opportunity_mirror_payload(sqlite_path)
    source_counts = payload_counts(payload)

    result: dict[str, Any] = {
        "dry_run": dry_run,
        "applied": False,
        "source_counts": source_counts,
        "existing_postgres_counts": {},
        "written_counts": {},
        "enrichment_counts": enrichment_counts(payload),
        "schema_version": payload["meta"]["schema_version"],
        "build_contract": payload["meta"]["build_contract"],
        "pr3_built_at": payload["meta"]["built_at"],
        "identity_fingerprint": payload["meta"]["identity_fingerprint"],
        "identity_fingerprint_match_status": payload["meta"][
            "identity_fingerprint_match_status"
        ],
        "opportunity_source_fingerprint": payload["meta"][
            "opportunity_source_fingerprint"
        ],
        "source_freshness_verified": True,
    }

    _require_psycopg()
    assert psycopg is not None

    with psycopg.connect(pg_url, autocommit=False) as pg_conn:
        with pg_conn.cursor() as cur:
            if not pg_commercial_opportunity_tables_exist(cur):
                raise CommercialOpportunityMirrorSafetyError(
                    "ARCH-2A Postgres tables are missing; run Alembic 0031 "
                    "on the intended scratch/staging target first"
                )

            existing_counts = postgres_commercial_opportunity_counts(cur)
            result["existing_postgres_counts"] = existing_counts

            assert_safe_replace(
                source_counts,
                existing_counts,
                min_replace_ratio=min_replace_ratio,
            )

            if dry_run:
                pg_conn.rollback()
                return result

            synced_at = datetime.now(timezone.utc)

            # No destructive write occurs before all source validation and
            # replacement-collapse checks above have succeeded.
            for schema, table in DELETE_ORDER:
                cur.execute(f"DELETE FROM {schema}.{table}")

            _insert_payload(
                cur,
                payload,
                synced_at=synced_at,
            )

            written_counts = postgres_commercial_opportunity_counts(cur)

            expected_pg_counts = {
                "opportunity": source_counts["opportunity"],
                "opportunity_event": source_counts["opportunity_event"],
                "opportunity_evidence": source_counts["opportunity_evidence"],
                "opportunity_conflict": source_counts["opportunity_conflict"],
            }

            if written_counts != expected_pg_counts:
                raise CommercialOpportunityMirrorSafetyError(
                    "Postgres count validation failed inside transaction: "
                    f"expected={expected_pg_counts} actual={written_counts}"
                )

            cur.execute(
                """
                SELECT COUNT(*)
                FROM commercial.opportunity_event e
                LEFT JOIN commercial.opportunity o
                  ON o.opportunity_id = e.opportunity_id
                WHERE o.opportunity_id IS NULL
                """
            )
            orphan_events = int(cur.fetchone()[0])

            cur.execute(
                """
                SELECT COUNT(*)
                FROM commercial.opportunity_evidence e
                LEFT JOIN commercial.opportunity o
                  ON o.opportunity_id = e.opportunity_id
                WHERE o.opportunity_id IS NULL
                """
            )
            orphan_evidence = int(cur.fetchone()[0])

            cur.execute(
                """
                SELECT COUNT(*)
                FROM commercial.opportunity_conflict c
                LEFT JOIN commercial.opportunity o
                  ON o.opportunity_id = c.opportunity_id
                WHERE c.opportunity_id IS NOT NULL
                  AND o.opportunity_id IS NULL
                """
            )
            orphan_conflicts = int(cur.fetchone()[0])

            if orphan_events or orphan_evidence or orphan_conflicts:
                raise CommercialOpportunityMirrorSafetyError(
                    "Postgres FK/integrity validation failed inside transaction: "
                    f"orphan_events={orphan_events}, "
                    f"orphan_evidence={orphan_evidence}, "
                    f"orphan_conflicts={orphan_conflicts}"
                )

            pg_conn.commit()

            result["applied"] = True
            result["written_counts"] = written_counts
            result["synced_at"] = synced_at.isoformat()

    return result


__all__ = [
    "CommercialOpportunityMirrorSafetyError",
    "MIN_REPLACE_RATIO",
    "PG_TABLES",
    "assert_safe_replace",
    "enrichment_counts",
    "load_commercial_opportunity_mirror_payload",
    "payload_counts",
    "pg_commercial_opportunity_tables_exist",
    "postgres_commercial_opportunity_counts",
    "sync_commercial_opportunity_postgres_mirror",
    "validate_build_meta",
    "validate_payload_integrity",
    "validate_source_freshness",
]
