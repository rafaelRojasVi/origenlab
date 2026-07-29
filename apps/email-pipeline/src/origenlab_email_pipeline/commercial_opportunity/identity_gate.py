"""PR2 identity snapshot compatibility gate for PR3 apply."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from origenlab_email_pipeline.commercial_opportunity.constants import (
    REQUIRED_IDENTITY_SCHEMA_VERSION,
)


class IdentitySnapshotError(RuntimeError):
    """Raised when persisted PR2 identity snapshot is missing or incompatible."""


@dataclass(frozen=True)
class IdentitySnapshotMeta:
    schema_version: str | None
    identity_fingerprint: str | None
    present: bool


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def load_identity_snapshot_meta(conn: sqlite3.Connection) -> IdentitySnapshotMeta:
    if not _table_exists(conn, "commercial_identity_build_meta"):
        return IdentitySnapshotMeta(schema_version=None, identity_fingerprint=None, present=False)
    rows = {
        str(r[0]): str(r[1])
        for r in conn.execute(
            "SELECT meta_key, meta_value FROM commercial_identity_build_meta"
        ).fetchall()
    }
    if not rows:
        return IdentitySnapshotMeta(schema_version=None, identity_fingerprint=None, present=False)
    return IdentitySnapshotMeta(
        schema_version=rows.get("schema_version"),
        identity_fingerprint=rows.get("identity_fingerprint"),
        present=True,
    )


def verify_identity_snapshot(
    *,
    snapshot: IdentitySnapshotMeta,
    expected_fingerprint: str,
    required_schema_version: str = REQUIRED_IDENTITY_SCHEMA_VERSION,
) -> str:
    """Return match status or raise IdentitySnapshotError for apply blockers.

    Status values: matched | mismatched | missing
    """
    if not snapshot.present or not snapshot.identity_fingerprint or not snapshot.schema_version:
        raise IdentitySnapshotError(
            "stale_or_missing_identity_snapshot: persisted PR2 identity snapshot is missing; "
            "run commercial identity --apply before opportunity --apply"
        )
    if snapshot.schema_version != required_schema_version:
        raise IdentitySnapshotError(
            f"stale_or_missing_identity_snapshot: identity schema_version "
            f"{snapshot.schema_version!r} != required {required_schema_version!r}"
        )
    if snapshot.identity_fingerprint != expected_fingerprint:
        raise IdentitySnapshotError(
            "stale_or_missing_identity_snapshot: identity fingerprint mismatch "
            "(persisted PR2 snapshot incompatible with current identity resolution)"
        )
    return "matched"
