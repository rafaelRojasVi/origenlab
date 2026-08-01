"""Plane A — read-only PR4 commercial_procurement_* evidence."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.commercial_identity.builder import (
    CommercialIdentityPathError,
    require_explicit_sqlite_path,
)
from origenlab_email_pipeline.commercial_procurement.constants import (
    BUILD_CONTRACT,
    SCHEMA_VERSION,
)
from origenlab_email_pipeline.commercial_procurement.schema import (
    SchemaIncompatibilityError,
    assert_schema_compatible,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.constants import (
    EVIDENCE_PLANE_PR4,
    REQUIRED_PR4_META_KEYS,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.models import (
    Pr4PlaneBundle,
    ProcurementEvidenceRef,
)
from origenlab_email_pipeline.commercial_procurement_candidate_planner.normalize import (
    accept_pr4_canonical_key,
    stable_content_id,
)


class Pr4PlaneError(ValueError):
    """Incompatible or incomplete PR4 plane."""


def open_pr4_readonly(sqlite_path: Path) -> sqlite3.Connection:
    path = require_explicit_sqlite_path(sqlite_path)
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _rows(conn: sqlite3.Connection, sql: str) -> tuple[dict[str, Any], ...]:
    return tuple(dict(r) for r in conn.execute(sql))


def load_pr4_plane(sqlite_path: Path) -> Pr4PlaneBundle:
    """Load persisted PR4 tables; refuse incompatible/incomplete schemas."""
    conn = open_pr4_readonly(sqlite_path)
    try:
        schema = assert_schema_compatible(conn)
        if schema.get("status") == "absent":
            raise Pr4PlaneError("PR4 commercial_procurement schema absent")
        meta_rows = _rows(
            conn, "SELECT meta_key, meta_value FROM commercial_procurement_build_meta"
        )
        build_meta = {str(r["meta_key"]): str(r["meta_value"]) for r in meta_rows}
        missing = [k for k in REQUIRED_PR4_META_KEYS if k not in build_meta or not build_meta[k]]
        if missing:
            raise Pr4PlaneError(f"missing required PR4 fingerprints/meta: {missing}")
        if build_meta.get("schema_version") != SCHEMA_VERSION:
            raise Pr4PlaneError(
                f"incompatible PR4 schema_version={build_meta.get('schema_version')!r}"
            )
        if build_meta.get("build_contract") != BUILD_CONTRACT:
            raise Pr4PlaneError(
                f"incompatible PR4 build_contract={build_meta.get('build_contract')!r}"
            )
        signals = _rows(
            conn,
            "SELECT * FROM commercial_procurement_signal ORDER BY procurement_id",
        )
        resolutions = _rows(
            conn,
            "SELECT * FROM commercial_procurement_account_resolution "
            "ORDER BY resolution_id",
        )
        evidence = _rows(
            conn,
            "SELECT * FROM commercial_procurement_evidence ORDER BY evidence_id",
        )
        conflicts = _rows(
            conn,
            "SELECT * FROM commercial_procurement_conflict ORDER BY conflict_id",
        )
        return Pr4PlaneBundle(
            signals=signals,
            account_resolutions=resolutions,
            evidence_rows=evidence,
            conflict_rows=conflicts,
            build_meta=build_meta,
            source_fingerprint=build_meta["source_fingerprint"],
            build_plan_fingerprint=build_meta["build_plan_fingerprint"],
            semantic_plan_digest=build_meta["semantic_plan_digest"],
            schema_version=build_meta["schema_version"],
            build_contract=build_meta["build_contract"],
            identity_fingerprint=build_meta["identity_fingerprint"],
            as_of_date=build_meta["as_of_date"],
            schema_status=str(schema.get("status") or "ok"),
        )
    except SchemaIncompatibilityError as exc:
        raise Pr4PlaneError(str(exc)) from exc
    except CommercialIdentityPathError:
        raise
    finally:
        conn.close()


def pr4_signals_to_evidence_refs(
    pr4: Pr4PlaneBundle,
) -> tuple[list[ProcurementEvidenceRef], list[dict[str, Any]]]:
    """Map accepted PR4 signals → evidence refs; collect skipped unverified keys."""
    resolution_by_proc = {
        str(r["procurement_id"]): r for r in pr4.account_resolutions
    }
    refs: list[ProcurementEvidenceRef] = []
    skipped: list[dict[str, Any]] = []
    for sig in pr4.signals:
        key = accept_pr4_canonical_key(
            sig.get("canonical_tender_key"),
            sig.get("tender_key_kind"),
        )
        if key is None:
            skipped.append(
                {
                    "procurement_id": sig.get("procurement_id"),
                    "reason": "pr4_canonical_key_not_accepted",
                }
            )
            continue
        proc_id = str(sig["procurement_id"])
        res = resolution_by_proc.get(proc_id)
        constituents = sig.get("constituent_source_ids_json") or "[]"
        try:
            source_ids = json.loads(constituents)
        except json.JSONDecodeError:
            source_ids = []
        source_record_id = (
            str(source_ids[0])
            if isinstance(source_ids, list) and source_ids
            else proc_id
        )
        ref_id = stable_content_id(
            "evidence_ref",
            {
                "plane": EVIDENCE_PLANE_PR4,
                "procurement_id": proc_id,
                "canonical_tender_key": key,
                "source_fingerprint": pr4.source_fingerprint,
            },
        )
        refs.append(
            ProcurementEvidenceRef(
                evidence_ref_id=ref_id,
                evidence_plane=EVIDENCE_PLANE_PR4,
                source_kind="pr4",
                endpoint_kind="pr4_persisted_signal",
                source_record_id=source_record_id,
                canonical_tender_key=key,
                snapshot_id=None,
                observation_id=None,
                acquired_at_utc=None,
                source_status_code=sig.get("status_code"),
                source_status_name=sig.get("status_name"),
                source_status_value=sig.get("status_name"),
                publication_timestamp_raw=sig.get("publication_at"),
                close_timestamp_raw=sig.get("close_at"),
                buyer_display_raw=sig.get("buyer_name_raw"),
                buyer_source_id=sig.get("buyer_domain_norm"),
                title_raw=sig.get("title"),
                source_payload_digest=sig.get("constituent_lines_fp"),
                source_fingerprint=pr4.source_fingerprint,
                normalized_semantic_digest=pr4.semantic_plan_digest,
                field_provenance={
                    "status": "pr4_signal",
                    "close": "pr4_signal",
                    "publication": "pr4_signal",
                    "buyer_display": "pr4_signal",
                    "buyer_source_id": "pr4_signal",
                    "title": "pr4_signal",
                    "account_resolution_pointer": (
                        str(res["resolution_id"]) if res else "absent"
                    ),
                },
                reason_codes=("pr4_persisted_signal",),
                source_rank_class="pr4",
                has_status=bool(sig.get("status_code") or sig.get("status_name")),
                has_close=bool(sig.get("close_at")),
                has_publication=bool(sig.get("publication_at")),
                has_buyer_display=bool(sig.get("buyer_name_raw")),
                has_buyer_source_id=bool(sig.get("buyer_domain_norm")),
                has_title=bool(sig.get("title")),
                pr4_procurement_id=proc_id,
                pr4_account_resolution_id=(
                    str(res["resolution_id"]) if res else None
                ),
                page_completeness="complete",
            )
        )
    return refs, skipped
