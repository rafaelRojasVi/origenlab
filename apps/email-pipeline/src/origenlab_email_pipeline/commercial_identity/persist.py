"""Transactional persistence for the commercial identity read model."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from origenlab_email_pipeline.commercial_identity.constants import SCHEMA_VERSION
from origenlab_email_pipeline.commercial_identity.models import IdentityResolution
from origenlab_email_pipeline.commercial_identity.schema import (
    clear_rebuildable_identity_tables,
    ensure_commercial_identity_tables,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_identity_resolution(conn: sqlite3.Connection, resolution: IdentityResolution) -> dict[str, int]:
    """Replace rebuildable identity tables with ``resolution`` inside the caller's transaction."""
    ensure_commercial_identity_tables(conn)
    clear_rebuildable_identity_tables(conn)

    account_rows = [
        (
            a.account_id,
            a.canonical_name,
            a.normalized_name,
            a.primary_domain,
            a.first_evidence_at,
            a.last_evidence_at,
            a.identity_confidence,
            a.identity_status,
        )
        for a in resolution.accounts
    ]
    if account_rows:
        conn.executemany(
            """
            INSERT INTO commercial_identity_account (
              account_id, canonical_name, normalized_name, primary_domain,
              first_evidence_at, last_evidence_at, identity_confidence, identity_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            account_rows,
        )

    alias_rows: list[tuple[Any, ...]] = []
    domain_rows: list[tuple[Any, ...]] = []
    for a in resolution.accounts:
        for alias in a.aliases.values():
            alias_rows.append(
                (
                    a.account_id,
                    alias["alias_name"],
                    alias["normalized_alias"],
                    int(alias.get("evidence_count") or 1),
                    alias.get("first_evidence_at"),
                    alias.get("last_evidence_at"),
                )
            )
        for dom in a.domains.values():
            domain_rows.append(
                (
                    a.account_id,
                    dom["domain_norm"],
                    int(dom["is_institutional"]),
                    dom["link_method"],
                    dom.get("first_evidence_at"),
                    dom.get("last_evidence_at"),
                )
            )
    if alias_rows:
        conn.executemany(
            """
            INSERT INTO commercial_identity_account_alias (
              account_id, alias_name, normalized_alias, evidence_count,
              first_evidence_at, last_evidence_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            alias_rows,
        )
    if domain_rows:
        conn.executemany(
            """
            INSERT INTO commercial_identity_account_domain (
              account_id, domain_norm, is_institutional, link_method,
              first_evidence_at, last_evidence_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            domain_rows,
        )

    contact_rows = [
        (
            c.contact_id,
            c.normalized_email,
            c.display_name,
            c.role,
            c.account_id,
            c.account_link_method,
            c.first_evidence_at,
            c.last_evidence_at,
            c.identity_confidence,
            c.identity_status,
            c.email_domain,
        )
        for c in resolution.contacts
    ]
    if contact_rows:
        conn.executemany(
            """
            INSERT INTO commercial_identity_contact (
              contact_id, normalized_email, display_name, role, account_id,
              account_link_method, first_evidence_at, last_evidence_at,
              identity_confidence, identity_status, email_domain
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            contact_rows,
        )

    evidence_rows = [
        (
            e.evidence_id,
            e.subject_kind,
            e.subject_id,
            e.source_table,
            e.source_record_id,
            e.source_plane,
            e.origin_plane,
            e.evidence_type,
            e.evidence_at,
            e.matching_reason_code,
            e.confidence,
            e.detail_json,
        )
        for e in resolution.evidence
    ]
    if evidence_rows:
        conn.executemany(
            """
            INSERT INTO commercial_identity_evidence (
              evidence_id, subject_kind, subject_id, source_table, source_record_id,
              source_plane, origin_plane, evidence_type, evidence_at,
              matching_reason_code, confidence, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            evidence_rows,
        )

    conflict_rows = [
        (
            c.conflict_id,
            c.conflict_type,
            c.reason_code,
            c.subject_kind,
            c.subject_keys_json,
            c.evidence_pointers_json,
            c.identity_status,
            c.detail_json,
        )
        for c in resolution.conflicts
    ]
    if conflict_rows:
        conn.executemany(
            """
            INSERT INTO commercial_identity_conflict (
              conflict_id, conflict_type, reason_code, subject_kind,
              subject_keys_json, evidence_pointers_json, identity_status, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            conflict_rows,
        )

    meta = {
        "schema_version": SCHEMA_VERSION,
        "built_at": _now_iso(),
        "metrics_json": json.dumps(resolution.metrics, sort_keys=True),
    }
    for k, v in meta.items():
        conn.execute(
            """
            INSERT INTO commercial_identity_build_meta(meta_key, meta_value)
            VALUES (?, ?)
            ON CONFLICT(meta_key) DO UPDATE SET meta_value=excluded.meta_value
            """,
            (k, v),
        )

    return {
        "accounts": len(account_rows),
        "aliases": len(alias_rows),
        "domains": len(domain_rows),
        "contacts": len(contact_rows),
        "evidence": len(evidence_rows),
        "conflicts": len(conflict_rows),
    }
