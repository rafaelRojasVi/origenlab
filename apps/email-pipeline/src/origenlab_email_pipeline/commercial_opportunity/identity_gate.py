"""PR2 identity snapshot compatibility gate for PR3 apply and dry-run reporting."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from origenlab_email_pipeline.commercial_identity.fingerprint import FINGERPRINT_ALGORITHM_VERSION
from origenlab_email_pipeline.commercial_opportunity.constants import (
    REQUIRED_IDENTITY_FINGERPRINT_ALGORITHM_VERSION,
    REQUIRED_IDENTITY_SCHEMA_VERSION,
)


class IdentitySnapshotError(RuntimeError):
    """Raised when persisted PR2 identity snapshot is missing or incompatible."""


class StaleBuildPlanError(RuntimeError):
    """Raised when identity or opportunity sources changed between plan and apply."""


@dataclass(frozen=True)
class IdentitySnapshotMeta:
    schema_version: str | None
    identity_fingerprint: str | None
    fingerprint_algorithm_version: str | None
    present: bool


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def load_identity_snapshot_meta(conn: sqlite3.Connection) -> IdentitySnapshotMeta:
    if not _table_exists(conn, "commercial_identity_build_meta"):
        return IdentitySnapshotMeta(
            schema_version=None,
            identity_fingerprint=None,
            fingerprint_algorithm_version=None,
            present=False,
        )
    rows = {
        str(r[0]): str(r[1])
        for r in conn.execute(
            "SELECT meta_key, meta_value FROM commercial_identity_build_meta"
        ).fetchall()
    }
    if not rows:
        return IdentitySnapshotMeta(
            schema_version=None,
            identity_fingerprint=None,
            fingerprint_algorithm_version=None,
            present=False,
        )
    return IdentitySnapshotMeta(
        schema_version=rows.get("schema_version"),
        identity_fingerprint=rows.get("identity_fingerprint"),
        fingerprint_algorithm_version=rows.get("identity_fingerprint_algorithm_version"),
        present=True,
    )


def classify_identity_snapshot(
    *,
    snapshot: IdentitySnapshotMeta,
    expected_fingerprint: str,
    required_schema_version: str = REQUIRED_IDENTITY_SCHEMA_VERSION,
    required_algorithm_version: str = REQUIRED_IDENTITY_FINGERPRINT_ALGORITHM_VERSION,
) -> str:
    """Return match status without raising.

    Statuses: matched | missing | schema_mismatch | version_mismatch | mismatched
    """
    if (
        not snapshot.present
        or not snapshot.identity_fingerprint
        or not snapshot.schema_version
        or not snapshot.fingerprint_algorithm_version
    ):
        return "missing"
    if snapshot.schema_version != required_schema_version:
        return "schema_mismatch"
    if snapshot.fingerprint_algorithm_version != required_algorithm_version:
        return "version_mismatch"
    if snapshot.identity_fingerprint != expected_fingerprint:
        return "mismatched"
    # Defensive: algorithm constant drift
    if required_algorithm_version != FINGERPRINT_ALGORITHM_VERSION:
        return "version_mismatch"
    return "matched"


def verify_identity_snapshot(
    *,
    snapshot: IdentitySnapshotMeta,
    expected_fingerprint: str,
    required_schema_version: str = REQUIRED_IDENTITY_SCHEMA_VERSION,
    required_algorithm_version: str = REQUIRED_IDENTITY_FINGERPRINT_ALGORITHM_VERSION,
) -> str:
    """Return ``matched`` or raise IdentitySnapshotError for apply blockers."""
    status = classify_identity_snapshot(
        snapshot=snapshot,
        expected_fingerprint=expected_fingerprint,
        required_schema_version=required_schema_version,
        required_algorithm_version=required_algorithm_version,
    )
    if status == "matched":
        return "matched"
    if status == "missing":
        raise IdentitySnapshotError(
            "stale_or_missing_identity_snapshot: persisted PR2 identity snapshot is missing; "
            "run commercial identity --apply before opportunity --apply"
        )
    if status == "schema_mismatch":
        raise IdentitySnapshotError(
            f"stale_or_missing_identity_snapshot: identity schema_version "
            f"{snapshot.schema_version!r} != required {required_schema_version!r}"
        )
    if status == "version_mismatch":
        raise IdentitySnapshotError(
            f"stale_or_missing_identity_snapshot: identity fingerprint algorithm "
            f"{snapshot.fingerprint_algorithm_version!r} != required {required_algorithm_version!r}"
        )
    raise IdentitySnapshotError(
        "stale_or_missing_identity_snapshot: identity fingerprint mismatch "
        "(persisted PR2 snapshot incompatible with current identity resolution)"
    )
