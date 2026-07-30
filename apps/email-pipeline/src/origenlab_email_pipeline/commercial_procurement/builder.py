"""Orchestration for commercial procurement dry-run and apply (PR4).

Transaction contract B_schema_additive_data_atomic:
additive schema may remain after first-run DDL; DELETE+INSERT is atomic with
foreign_keys=ON and BEGIN IMMEDIATE. Stale-plan checks run before any DELETE.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from origenlab_email_pipeline.commercial_identity.builder import (
    CommercialIdentityPathError,
    normalize_run_context,
    require_explicit_sqlite_path,
    validate_run_context_mode,
)
from origenlab_email_pipeline.commercial_identity.constants import (
    RUN_CONTEXT_LOCAL_FIXTURE,
    RUN_CONTEXT_PRODUCTION_APPLY,
    RUN_CONTEXT_SYNTHETIC_FIXTURE,
)
from origenlab_email_pipeline.commercial_procurement.constants import (
    AS_OF_TIMEZONE,
    BUILD_CONTRACT,
    BUSY_TIMEOUT_MS,
    PROCUREMENT_BUILD_PLAN_FP_ALGORITHM,
    PROCUREMENT_MATERIALIZATION_DIGEST_ALGORITHM,
    PROCUREMENT_SEMANTIC_PLAN_DIGEST_ALGORITHM,
    PROCUREMENT_SOURCE_FP_ALGORITHM,
    PROCUREMENT_TABLE_INSERT_ORDER,
    REQUIRED_IDENTITY_FINGERPRINT_ALGORITHM,
    RESOLVER_BUILD_CONTRACT_VERSION,
    SCHEMA_VERSION,
    TRANSACTION_CONTRACT,
)
from origenlab_email_pipeline.commercial_procurement.ids import (
    canonical_json,
    materialization_digest,
    semantic_plan_digest,
)
from origenlab_email_pipeline.commercial_procurement.models import (
    BuildMetaRow,
    ConflictRow,
    ProcurementPlan,
)
from origenlab_email_pipeline.commercial_procurement.persist import write_procurement_plan
from origenlab_email_pipeline.commercial_procurement.planner import (
    IdentityGateError,
    PlanValidationError,
    plan_procurement_from_connection,
)
from origenlab_email_pipeline.commercial_procurement.schema import (
    SchemaIncompatibilityError,
    assert_schema_compatible,
    clear_rebuildable_procurement_tables,
    ensure_commercial_procurement_tables,
)
from origenlab_email_pipeline.commercial_procurement.sources import SourceSchemaError
from origenlab_email_pipeline.commercial_procurement.sources import (
    disable_require_active_read_transaction,
    enable_require_active_read_transaction,
    load_pr3_immutability_sentinel,
)
from origenlab_email_pipeline.commercial_procurement.validate_temp import (
    TempSchemaValidationError,
    validate_plan_in_temp_sqlite,
)

InjectHook = Callable[[sqlite3.Connection], None] | None


class ApplyNotImplementedError(CommercialIdentityPathError):
    """Legacy alias kept for import compatibility."""


class StaleProcurementBuildPlanError(ValueError):
    """Approved plan no longer matches live write-transaction plan."""


class PersistenceValidationError(ValueError):
    """Post-write validation failed before commit."""


class UnsafeInvocationError(CommercialIdentityPathError):
    """Missing/invalid production approval inputs or unsafe invocation."""


@dataclass(frozen=True)
class ProcurementBuildResult:
    sqlite_path: Path
    plan: ProcurementPlan
    summary: dict[str, Any]
    temp_schema_counts: dict[str, int]


def connect_production_readonly(sqlite_path: Path) -> sqlite3.Connection:
    path = sqlite_path.resolve()
    if not path.is_file():
        raise CommercialIdentityPathError(f"sqlite path does not exist: {path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def connect_writable(sqlite_path: Path) -> sqlite3.Connection:
    path = sqlite_path.resolve()
    if not path.is_file():
        raise CommercialIdentityPathError(f"sqlite path does not exist: {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def assert_no_write_connection(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("CREATE TEMP TABLE _procurement_write_probe(x INTEGER)")
    except sqlite3.OperationalError:
        return
    raise CommercialIdentityPathError("SQLite connection is writable; expected read-only")


def parse_as_of_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise CommercialIdentityPathError(
            f"--as-of-date must be YYYY-MM-DD; got {value!r}"
        ) from exc


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


_PLACEHOLDER_EXPECTED = frozenset({"", "none", "null", "0", "placeholder", "todo"})
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


def _require_expected_digest(name: str, value: str | None) -> str:
    """Require exactly 64 lowercase hex chars (exit 6 on malformed)."""
    raw = (value or "").strip()
    if not raw or raw.lower() in _PLACEHOLDER_EXPECTED:
        raise UnsafeInvocationError(
            f"production_apply requires non-empty {name} from an approved dry-run"
        )
    if raw != raw.lower() or not _HEX64_RE.fullmatch(raw):
        raise UnsafeInvocationError(
            f"{name} must be exactly 64 lowercase hexadecimal characters"
        )
    return raw


def _require_expected_value(name: str, value: str | None) -> str:
    """Back-compat alias used by older call sites."""
    return _require_expected_digest(name, value)


def plan_to_json_summary(
    plan: ProcurementPlan,
    *,
    sqlite_path: Path,
    generated_at_utc: str,
    snapshot_diagnostics: dict[str, Any] | None = None,
    mode: str = "dry-run",
    applied: bool = False,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "mode": mode,
        "applied": applied,
        "sqlite_path": str(sqlite_path.resolve()),
        "schema_version": SCHEMA_VERSION,
        "build_contract": BUILD_CONTRACT,
        "resolver_build_contract_version": RESOLVER_BUILD_CONTRACT_VERSION,
        "transaction_contract": TRANSACTION_CONTRACT,
        "source_fingerprint_algorithm": PROCUREMENT_SOURCE_FP_ALGORITHM,
        "source_fingerprint": plan.source_fingerprint,
        "source_fingerprint_components": plan.source_fingerprint_components,
        "identity_fingerprint_algorithm_version": plan.identity_fingerprint_algorithm_version,
        "identity_fingerprint": plan.identity_fingerprint,
        "build_plan_fingerprint_algorithm": PROCUREMENT_BUILD_PLAN_FP_ALGORITHM,
        "build_plan_fingerprint": plan.build_plan_fingerprint,
        "semantic_plan_digest_algorithm": PROCUREMENT_SEMANTIC_PLAN_DIGEST_ALGORITHM,
        "semantic_plan_digest": plan.semantic_plan_digest,
        "materialization_digest": plan.materialization_digest,
        "materialization_digest_algorithm": (
            PROCUREMENT_MATERIALIZATION_DIGEST_ALGORITHM
            if plan.materialization_digest
            else None
        ),
        "generated_at_utc": generated_at_utc,
        "as_of_date": plan.as_of_date,
        "as_of_timezone": AS_OF_TIMEZONE,
        "run_context": plan.run_context,
        "source_outcome_counts": {
            "all": plan.metrics["source_outcome_count"],
            "verified_lines": plan.metrics["verified_source_line_count"],
            "unresolved": plan.metrics["unresolved_source_row_count"],
        },
        "signal_count": plan.metrics["signal_count"],
        "resolution_distribution": plan.metrics["resolution_distribution"],
        "route_distribution": plan.metrics["route_distribution"],
        "procurement_context_distribution": plan.metrics["procurement_context_distribution"],
        "evidence_count": plan.metrics["evidence_count"],
        "conflict_distribution": plan.metrics["conflict_distribution"],
        "field_plane_conflict_distribution": plan.metrics.get(
            "field_plane_conflict_distribution", {}
        ),
        "enrichment_distribution": plan.metrics["enrichment_distribution"],
        "operator_queue_eligible_count": plan.metrics["operator_queue_eligible_count"],
        "unique_linked_accounts": plan.metrics["unique_linked_accounts"],
        "metrics": plan.metrics,
        "planned_writes": {
            "commercial_procurement_signal": len(plan.signals),
            "commercial_procurement_account_resolution": len(plan.resolutions),
            "commercial_procurement_evidence": len(plan.evidence),
            "commercial_procurement_conflict": len(plan.conflicts),
            "commercial_procurement_enrichment_candidate": len(plan.enrichment_candidates),
            "commercial_procurement_build_meta": len(plan.build_meta),
        },
        "required_identity_fingerprint_algorithm": REQUIRED_IDENTITY_FINGERPRINT_ALGORITHM,
        "snapshot_diagnostics": snapshot_diagnostics or {},
    }
    if extra:
        summary.update(extra)
    return summary


def _preflight_plan(
    *,
    path: Path,
    as_of: date,
    ctx: str,
    generated_at: str,
    validate_temp_schema: bool,
) -> tuple[ProcurementPlan, frozenset[str], dict[str, Any], dict[str, int]]:
    conn = connect_production_readonly(path)
    plan: ProcurementPlan | None = None
    known: frozenset[str] = frozenset()
    snapshot_diagnostics: dict[str, Any] = {}
    try:
        assert_no_write_connection(conn)
        conn.execute("BEGIN DEFERRED")
        enable_require_active_read_transaction(conn)
        if not conn.in_transaction:
            raise CommercialIdentityPathError("failed to start deferred read transaction")
        data_version_before = conn.execute("PRAGMA data_version").fetchone()[0]
        plan, snap = plan_procurement_from_connection(
            conn=conn,
            as_of_date=as_of,
            run_context=ctx,
            generated_at_utc=generated_at,
        )
        known = snap["known_account_ids"]
        data_version_after = conn.execute("PRAGMA data_version").fetchone()[0]
        snapshot_diagnostics = {
            "read_transaction": "deferred",
            "data_version_before": data_version_before,
            "data_version_after": data_version_after,
            "pr3_sentinel": snap["pr3_sentinel"],
            "identity_fingerprint": plan.identity_fingerprint,
            "source_fingerprint": plan.source_fingerprint,
        }
        conn.rollback()
    except Exception:
        try:
            if conn.in_transaction:
                conn.rollback()
        except sqlite3.Error:
            pass
        raise
    finally:
        disable_require_active_read_transaction(conn)
        conn.close()

    assert plan is not None
    temp_counts: dict[str, int] = {}
    if validate_temp_schema:
        temp_counts = validate_plan_in_temp_sqlite(
            plan,
            known_account_ids=known,
            source_pointer_registry=plan.source_pointer_registry,
        )
    return plan, known, snapshot_diagnostics, temp_counts


def _stamp_conflicts(plan: ProcurementPlan, stamp: str) -> ProcurementPlan:
    conflicts = tuple(
        ConflictRow(
            conflict_id=c.conflict_id,
            procurement_id=c.procurement_id,
            source_system=c.source_system,
            source_record_id=c.source_record_id,
            subject_kind=c.subject_kind,
            subject_key=c.subject_key,
            account_id=c.account_id,
            reason_code=c.reason_code,
            confidence=c.confidence,
            detail_json=c.detail_json,
            created_at=stamp,
        )
        for c in plan.conflicts
    )
    return ProcurementPlan(
        signals=plan.signals,
        resolutions=plan.resolutions,
        evidence=plan.evidence,
        conflicts=conflicts,
        enrichment_candidates=plan.enrichment_candidates,
        build_meta=plan.build_meta,
        source_fingerprint=plan.source_fingerprint,
        source_fingerprint_components=plan.source_fingerprint_components,
        build_plan_fingerprint=plan.build_plan_fingerprint,
        semantic_plan_digest=plan.semantic_plan_digest,
        identity_fingerprint=plan.identity_fingerprint,
        identity_fingerprint_algorithm_version=plan.identity_fingerprint_algorithm_version,
        as_of_date=plan.as_of_date,
        run_context=plan.run_context,
        metrics=plan.metrics,
        generated_at_utc=plan.generated_at_utc,
        materialization_digest=plan.materialization_digest,
        source_pointer_registry=plan.source_pointer_registry,
    )


def _build_apply_meta(
    plan: ProcurementPlan,
    *,
    run_context: str,
    materialized_at: str,
    expected: dict[str, str],
    written: dict[str, int],
) -> dict[str, str]:
    return {
        "schema_version": SCHEMA_VERSION,
        "build_contract": BUILD_CONTRACT,
        "resolver_build_contract_version": RESOLVER_BUILD_CONTRACT_VERSION,
        "transaction_contract": TRANSACTION_CONTRACT,
        "mode": "apply",
        "applied": "true",
        "run_context": run_context,
        "as_of_date": plan.as_of_date,
        "as_of_timezone": AS_OF_TIMEZONE,
        "source_fingerprint_algorithm": PROCUREMENT_SOURCE_FP_ALGORITHM,
        "source_fingerprint": plan.source_fingerprint,
        "source_fingerprint_components_json": canonical_json(
            plan.source_fingerprint_components
        ),
        "identity_fingerprint_algorithm_version": plan.identity_fingerprint_algorithm_version,
        "identity_fingerprint": plan.identity_fingerprint,
        "build_plan_fingerprint_algorithm": PROCUREMENT_BUILD_PLAN_FP_ALGORITHM,
        "build_plan_fingerprint": plan.build_plan_fingerprint,
        "semantic_plan_digest_algorithm": PROCUREMENT_SEMANTIC_PLAN_DIGEST_ALGORITHM,
        "semantic_plan_digest": plan.semantic_plan_digest,
        "materialization_digest_algorithm": PROCUREMENT_MATERIALIZATION_DIGEST_ALGORITHM,
        "materialization_digest": plan.materialization_digest or "",
        "generated_at_utc": plan.generated_at_utc or materialized_at,
        "materialized_at_utc": materialized_at,
        "expected_source_fingerprint": expected.get("source_fingerprint", ""),
        "expected_identity_fingerprint": expected.get("identity_fingerprint", ""),
        "expected_build_plan_fingerprint": expected.get("build_plan_fingerprint", ""),
        "expected_semantic_plan_digest": expected.get("semantic_plan_digest", ""),
        "stale_plan_recheck_status": "matched",
        "metrics_json": canonical_json(plan.metrics),
        "written_counts_json": canonical_json(written),
    }


def _read_semantic_rows_from_db(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for table in PROCUREMENT_TABLE_INSERT_ORDER:
        if table == "commercial_procurement_build_meta":
            continue
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        rows = []
        for row in conn.execute(f"SELECT * FROM {table}"):
            rows.append({c: row[c] for c in cols})
        out[table] = rows
    return out


def _validate_persisted_plan(
    conn: sqlite3.Connection,
    plan: ProcurementPlan,
    *,
    known_account_ids: frozenset[str],
) -> dict[str, Any]:
    counts = {
        table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in PROCUREMENT_TABLE_INSERT_ORDER
        if table != "commercial_procurement_build_meta"
    }
    expected_counts = {
        "commercial_procurement_signal": len(plan.signals),
        "commercial_procurement_account_resolution": len(plan.resolutions),
        "commercial_procurement_evidence": len(plan.evidence),
        "commercial_procurement_conflict": len(plan.conflicts),
        "commercial_procurement_enrichment_candidate": len(plan.enrichment_candidates),
    }
    for table, n in expected_counts.items():
        if counts[table] != n:
            raise PersistenceValidationError(
                f"row count mismatch {table}: db={counts[table]} plan={n}"
            )

    if counts["commercial_procurement_signal"] != counts[
        "commercial_procurement_account_resolution"
    ]:
        raise PersistenceValidationError("signal/resolution cardinality mismatch")

    for row in conn.execute(
        """
        SELECT resolution_status, account_id, candidate_account_ids_json
        FROM commercial_procurement_account_resolution
        """
    ):
        status = row["resolution_status"]
        if status == "linked":
            if not row["account_id"]:
                raise PersistenceValidationError("linked resolution missing account_id")
            if row["account_id"] not in known_account_ids:
                raise PersistenceValidationError(
                    f"linked account missing from PR2: {row['account_id']}"
                )
        elif row["account_id"] is not None:
            raise PersistenceValidationError(
                f"non-linked resolution must have null account_id ({status})"
            )
        cands = json.loads(row["candidate_account_ids_json"])
        for cand in cands:
            if cand not in known_account_ids:
                raise PersistenceValidationError(
                    f"candidate account missing from PR2: {cand}"
                )

    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    if fk:
        raise PersistenceValidationError(f"foreign_key_check failed: {list(fk)}")

    registry = plan.source_pointer_registry
    for row in conn.execute(
        """
        SELECT subject_kind, subject_id, source_table, source_record_id
        FROM commercial_procurement_evidence
        """
    ):
        kind, sid = row["subject_kind"], row["subject_id"]
        ok = False
        if kind == "signal":
            ok = conn.execute(
                "SELECT 1 FROM commercial_procurement_signal WHERE procurement_id=?",
                (sid,),
            ).fetchone() is not None
        elif kind == "resolution":
            ok = conn.execute(
                "SELECT 1 FROM commercial_procurement_account_resolution WHERE resolution_id=?",
                (sid,),
            ).fetchone() is not None
        elif kind in {
            "conflict",
            "unresolved_source",
            "line_conflict",
            "resolution_conflict",
        }:
            ok = conn.execute(
                "SELECT 1 FROM commercial_procurement_conflict WHERE conflict_id=?",
                (sid,),
            ).fetchone() is not None
        elif kind == "enrichment":
            ok = conn.execute(
                "SELECT 1 FROM commercial_procurement_enrichment_candidate WHERE candidate_id=?",
                (sid,),
            ).fetchone() is not None
        if not ok:
            raise PersistenceValidationError(
                f"evidence subject does not resolve: {kind}/{sid}"
            )
        if row["source_table"] in {"external_leads_raw", "lead_master"}:
            if (row["source_table"], row["source_record_id"]) not in registry:
                raise PersistenceValidationError(
                    f"evidence source pointer missing: {row['source_table']}/"
                    f"{row['source_record_id']}"
                )

    for row in conn.execute(
        """
        SELECT conflict_id, procurement_id, source_system, source_record_id
        FROM commercial_procurement_conflict
        """
    ):
        if row["procurement_id"] is None and not (
            row["source_system"] and row["source_record_id"]
        ):
            raise PersistenceValidationError(
                f"unresolved conflict {row['conflict_id']} missing direct provenance"
            )

    semantic_rows = _read_semantic_rows_from_db(conn)
    pk_map = {
        "commercial_procurement_signal": "procurement_id",
        "commercial_procurement_account_resolution": "resolution_id",
        "commercial_procurement_evidence": "evidence_id",
        "commercial_procurement_conflict": "conflict_id",
        "commercial_procurement_enrichment_candidate": "candidate_id",
    }
    for table, rows in semantic_rows.items():
        semantic_rows[table] = sorted(rows, key=lambda r: str(r[pk_map[table]]))

    readback_digest = semantic_plan_digest(table_rows=semantic_rows)
    if readback_digest != plan.semantic_plan_digest:
        raise PersistenceValidationError(
            "readback semantic digest mismatch vs live plan "
            f"(db={readback_digest} plan={plan.semantic_plan_digest})"
        )

    meta = {
        str(r["meta_key"]): str(r["meta_value"])
        for r in conn.execute(
            "SELECT meta_key, meta_value FROM commercial_procurement_build_meta"
        )
    }
    expected_meta = {
        "semantic_plan_digest": plan.semantic_plan_digest,
        "source_fingerprint": plan.source_fingerprint,
        "identity_fingerprint": plan.identity_fingerprint,
        "build_plan_fingerprint": plan.build_plan_fingerprint,
        "as_of_date": plan.as_of_date,
        "resolver_build_contract_version": RESOLVER_BUILD_CONTRACT_VERSION,
    }
    for key, value in expected_meta.items():
        if meta.get(key) != value:
            raise PersistenceValidationError(f"build_meta mismatch for {key}")

    return {
        "readback_counts": counts,
        "readback_semantic_digest": readback_digest,
        "foreign_key_check_count": 0,
        "build_meta_keys": sorted(meta.keys()),
    }


def _non_pr4_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    tables = [
        "commercial_identity_account",
        "commercial_identity_account_alias",
        "commercial_identity_account_domain",
        "commercial_identity_build_meta",
        "commercial_opportunity",
        "commercial_opportunity_event",
        "commercial_opportunity_evidence",
        "commercial_opportunity_conflict",
        "commercial_opportunity_build_meta",
        "lead_master",
        "external_leads_raw",
        "commercial_deal",
        "opportunity_signals",
    ]
    out: dict[str, Any] = {}
    for table in tables:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not exists:
            out[table] = None
            continue
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        cols = [d[0] for d in conn.execute(f"SELECT * FROM {table} LIMIT 0").description]
        out[table] = {
            "count": len(rows),
            "rows": [tuple(r) for r in rows],
            "columns": cols,
        }
    out["pr3_sentinel"] = load_pr3_immutability_sentinel(conn)
    return out


def _compare_snapshots(before: dict[str, Any], after: dict[str, Any]) -> dict[str, bool]:
    def _block_unchanged(keys: list[str]) -> bool:
        return all(before.get(k) == after.get(k) for k in keys)

    return {
        "identity_snapshot_unchanged": _block_unchanged(
            [
                "commercial_identity_account",
                "commercial_identity_account_alias",
                "commercial_identity_account_domain",
                "commercial_identity_build_meta",
            ]
        ),
        "opportunity_snapshot_unchanged": _block_unchanged(
            [
                "commercial_opportunity",
                "commercial_opportunity_event",
                "commercial_opportunity_evidence",
                "commercial_opportunity_conflict",
                "commercial_opportunity_build_meta",
            ]
        )
        and before.get("pr3_sentinel") == after.get("pr3_sentinel"),
        "procurement_sources_unchanged": _block_unchanged(
            ["lead_master", "external_leads_raw", "commercial_deal", "opportunity_signals"]
        ),
    }


def apply_procurement_plan(
    *,
    sqlite_path: Path,
    preflight: ProcurementPlan,
    as_of_date: date,
    run_context: str,
    expected: dict[str, str],
    inject_source_change: InjectHook = None,
    inject_identity_change: InjectHook = None,
    inject_after_schema: InjectHook = None,
    inject_after_delete: InjectHook = None,
    inject_mid_signal_insert: InjectHook = None,
    inject_before_validation: InjectHook = None,
    inject_before_commit: InjectHook = None,
    inject_bad_readback: InjectHook = None,
    force_foreign_keys_off: bool = False,
) -> dict[str, Any]:
    path = require_explicit_sqlite_path(sqlite_path)
    schema_conn = connect_writable(path)
    try:
        schema_status = ensure_commercial_procurement_tables(schema_conn)
    finally:
        schema_conn.close()

    if inject_after_schema is not None:
        probe = connect_writable(path)
        try:
            inject_after_schema(probe)
            probe.commit()
        finally:
            probe.close()

    conn = connect_writable(path)
    try:
        if force_foreign_keys_off:
            conn.execute("PRAGMA foreign_keys = OFF")
        else:
            conn.execute("PRAGMA foreign_keys = ON")
        fk = conn.execute("PRAGMA foreign_keys").fetchone()
        if not force_foreign_keys_off and (fk is None or int(fk[0]) != 1):
            raise PersistenceValidationError(
                "PRAGMA foreign_keys=ON failed; refusing unsafe procurement rebuild"
            )

        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        before = _non_pr4_snapshot(conn)
        materialized_at = _utc_now_iso()

        conn.execute("BEGIN IMMEDIATE")
        try:
            if inject_source_change is not None:
                inject_source_change(conn)
            if inject_identity_change is not None:
                inject_identity_change(conn)

            live, snap = plan_procurement_from_connection(
                conn=conn,
                as_of_date=as_of_date,
                run_context=run_context,
                materialization_stamp=materialized_at,
                generated_at_utc=preflight.generated_at_utc or materialized_at,
            )
            known = snap["known_account_ids"]

            if live.source_fingerprint != preflight.source_fingerprint:
                raise StaleProcurementBuildPlanError(
                    "stale_build_plan: source fingerprint changed between preflight and apply"
                )
            if live.identity_fingerprint != preflight.identity_fingerprint:
                raise StaleProcurementBuildPlanError(
                    "stale_build_plan: identity fingerprint changed between preflight and apply"
                )
            if live.build_plan_fingerprint != preflight.build_plan_fingerprint:
                raise StaleProcurementBuildPlanError(
                    "stale_build_plan: build-plan fingerprint changed between preflight and apply"
                )
            if live.semantic_plan_digest != preflight.semantic_plan_digest:
                raise StaleProcurementBuildPlanError(
                    "stale_build_plan: semantic plan digest changed between preflight and apply"
                )
            if live.as_of_date != preflight.as_of_date:
                raise StaleProcurementBuildPlanError("stale_build_plan: as_of_date mismatch")

            live = _stamp_conflicts(live, materialized_at)

            written_preview = {
                "commercial_procurement_signal": len(live.signals),
                "commercial_procurement_account_resolution": len(live.resolutions),
                "commercial_procurement_evidence": len(live.evidence),
                "commercial_procurement_conflict": len(live.conflicts),
                "commercial_procurement_enrichment_candidate": len(
                    live.enrichment_candidates
                ),
                "commercial_procurement_build_meta": 0,
            }
            provisional_meta = _build_apply_meta(
                live,
                run_context=run_context,
                materialized_at=materialized_at,
                expected=expected,
                written=written_preview,
            )
            full_rows = dict(live.semantic_table_rows())
            full_rows["commercial_procurement_build_meta"] = [
                {"meta_key": k, "meta_value": v}
                for k, v in sorted(provisional_meta.items())
            ]
            mat = materialization_digest(table_rows=full_rows)
            live = ProcurementPlan(
                signals=live.signals,
                resolutions=live.resolutions,
                evidence=live.evidence,
                conflicts=live.conflicts,
                enrichment_candidates=live.enrichment_candidates,
                build_meta=tuple(
                    BuildMetaRow(meta_key=k, meta_value=v)
                    for k, v in sorted(provisional_meta.items())
                ),
                source_fingerprint=live.source_fingerprint,
                source_fingerprint_components=live.source_fingerprint_components,
                build_plan_fingerprint=live.build_plan_fingerprint,
                semantic_plan_digest=live.semantic_plan_digest,
                identity_fingerprint=live.identity_fingerprint,
                identity_fingerprint_algorithm_version=(
                    live.identity_fingerprint_algorithm_version
                ),
                as_of_date=live.as_of_date,
                run_context=live.run_context,
                metrics=live.metrics,
                generated_at_utc=live.generated_at_utc,
                materialization_digest=mat,
                source_pointer_registry=live.source_pointer_registry,
            )
            meta = _build_apply_meta(
                live,
                run_context=run_context,
                materialized_at=materialized_at,
                expected=expected,
                written=written_preview,
            )
            meta["materialization_digest"] = mat

            if inject_mid_signal_insert is not None:
                clear_rebuildable_procurement_tables(conn)
                if live.signals:
                    s0 = live.signals[0]
                    conn.execute(
                        """
                        INSERT INTO commercial_procurement_signal (
                          procurement_id, source_system, canonical_tender_key, tender_key_kind,
                          buyer_name_raw, buyer_name_norm, buyer_domain_norm, buyer_email_norm,
                          region, title, status_code, status_name, publication_at, close_at,
                          procurement_context, context_reason_code, confidence, line_item_count,
                          constituent_source_ids_json, constituent_lines_fp,
                          first_seen_at, last_seen_at, review_status
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            s0.procurement_id,
                            s0.source_system,
                            s0.canonical_tender_key,
                            s0.tender_key_kind,
                            s0.buyer_name_raw,
                            s0.buyer_name_norm,
                            s0.buyer_domain_norm,
                            s0.buyer_email_norm,
                            s0.region,
                            s0.title,
                            s0.status_code,
                            s0.status_name,
                            s0.publication_at,
                            s0.close_at,
                            s0.procurement_context,
                            s0.context_reason_code,
                            s0.confidence,
                            s0.line_item_count,
                            s0.constituent_source_ids_json,
                            s0.constituent_lines_fp,
                            s0.first_seen_at,
                            s0.last_seen_at,
                            s0.review_status,
                        ),
                    )
                inject_mid_signal_insert(conn)
                raise PersistenceValidationError("injected mid-signal-insert failure")

            if inject_after_delete is not None:
                clear_rebuildable_procurement_tables(conn)
                inject_after_delete(conn)
                raise PersistenceValidationError("injected after-delete failure")

            written = write_procurement_plan(conn, live, build_meta=meta)
            meta["written_counts_json"] = canonical_json(written)
            conn.execute("DELETE FROM commercial_procurement_build_meta")
            for key in sorted(meta.keys()):
                conn.execute(
                    "INSERT INTO commercial_procurement_build_meta(meta_key, meta_value) "
                    "VALUES (?, ?)",
                    (key, meta[key]),
                )

            if inject_before_validation is not None:
                inject_before_validation(conn)
            if inject_bad_readback is not None:
                inject_bad_readback(conn)

            validation = _validate_persisted_plan(conn, live, known_account_ids=known)

            if inject_before_commit is not None:
                inject_before_commit(conn)

            conn.commit()
        except Exception:
            conn.rollback()
            raise

        after = _non_pr4_snapshot(conn)
        immutability = _compare_snapshots(before, after)
        return {
            "applied": True,
            "written": written,
            "plan": live,
            "materialized_at_utc": materialized_at,
            "schema_status": schema_status,
            "validation": validation,
            "immutability": immutability,
            "expected_match": {
                "source_fingerprint": "matched",
                "identity_fingerprint": "matched",
                "build_plan_fingerprint": "matched",
                "semantic_plan_digest": "matched",
            },
            "stale_plan_recheck_status": "matched",
            "known_account_ids": known,
        }
    finally:
        conn.close()


def run_procurement_build(
    *,
    sqlite_path: Path,
    as_of_date: date | str,
    run_context: str | None = None,
    apply: bool = False,
    validate_temp_schema: bool = True,
    expected_source_fingerprint: str | None = None,
    expected_identity_fingerprint: str | None = None,
    expected_build_plan_fingerprint: str | None = None,
    expected_semantic_plan_digest: str | None = None,
    allow_fixture_apply: bool = False,
    inject_source_change: InjectHook = None,
    inject_identity_change: InjectHook = None,
    inject_after_schema: InjectHook = None,
    inject_after_delete: InjectHook = None,
    inject_mid_signal_insert: InjectHook = None,
    inject_before_validation: InjectHook = None,
    inject_before_commit: InjectHook = None,
    inject_bad_readback: InjectHook = None,
    force_foreign_keys_off: bool = False,
) -> ProcurementBuildResult:
    path = require_explicit_sqlite_path(sqlite_path)
    ctx = normalize_run_context(run_context)
    validate_run_context_mode(run_context=ctx, apply=apply)
    as_of = parse_as_of_date(as_of_date) if isinstance(as_of_date, str) else as_of_date
    generated_at = _utc_now_iso()

    if apply and ctx == RUN_CONTEXT_PRODUCTION_APPLY and allow_fixture_apply:
        raise UnsafeInvocationError(
            "allow_fixture_apply is test-only and invalid with production_apply"
        )

    if apply and ctx != RUN_CONTEXT_PRODUCTION_APPLY:
        if not allow_fixture_apply:
            raise UnsafeInvocationError(
                "non-production --apply requires allow_fixture_apply=True "
                "(test-only capability; not available via CLI)"
            )
        if ctx not in {RUN_CONTEXT_LOCAL_FIXTURE, RUN_CONTEXT_SYNTHETIC_FIXTURE}:
            raise UnsafeInvocationError(
                f"fixture apply not permitted for run-context {ctx!r}"
            )

    expected: dict[str, str] = {}
    if apply and ctx == RUN_CONTEXT_PRODUCTION_APPLY:
        expected = {
            "source_fingerprint": _require_expected_digest(
                "--expected-source-fingerprint", expected_source_fingerprint
            ),
            "identity_fingerprint": _require_expected_digest(
                "--expected-identity-fingerprint", expected_identity_fingerprint
            ),
            "build_plan_fingerprint": _require_expected_digest(
                "--expected-build-plan-fingerprint", expected_build_plan_fingerprint
            ),
            "semantic_plan_digest": _require_expected_digest(
                "--expected-semantic-plan-digest", expected_semantic_plan_digest
            ),
        }
    elif apply and allow_fixture_apply:
        # Optional expected values on fixture apply must still be well-formed if present.
        for name, raw, key in (
            (
                "--expected-source-fingerprint",
                expected_source_fingerprint,
                "source_fingerprint",
            ),
            (
                "--expected-identity-fingerprint",
                expected_identity_fingerprint,
                "identity_fingerprint",
            ),
            (
                "--expected-build-plan-fingerprint",
                expected_build_plan_fingerprint,
                "build_plan_fingerprint",
            ),
            (
                "--expected-semantic-plan-digest",
                expected_semantic_plan_digest,
                "semantic_plan_digest",
            ),
        ):
            if raw is not None and str(raw).strip():
                expected[key] = _require_expected_digest(name, raw)

    if apply:
        probe = connect_writable(path)
        try:
            assert_schema_compatible(probe)
        finally:
            probe.close()

    plan, known, snapshot_diagnostics, temp_counts = _preflight_plan(
        path=path,
        as_of=as_of,
        ctx=ctx,
        generated_at=generated_at,
        validate_temp_schema=validate_temp_schema,
    )

    if not apply:
        summary = plan_to_json_summary(
            plan,
            sqlite_path=path,
            generated_at_utc=generated_at,
            snapshot_diagnostics=snapshot_diagnostics,
            mode="dry-run",
            applied=False,
            extra={
                "temp_schema_validation": {"ok": True, "row_counts": temp_counts},
                "schema_creation_status": "skipped_dry_run",
                "field_plane_conflict_distribution": plan.metrics.get(
                    "field_plane_conflict_distribution", {}
                ),
            },
        )
        return ProcurementBuildResult(
            sqlite_path=path,
            plan=plan,
            summary=summary,
            temp_schema_counts=temp_counts,
        )

    for key, attr in (
        ("source_fingerprint", "source_fingerprint"),
        ("identity_fingerprint", "identity_fingerprint"),
        ("build_plan_fingerprint", "build_plan_fingerprint"),
        ("semantic_plan_digest", "semantic_plan_digest"),
    ):
        if key in expected and expected[key] != getattr(plan, attr):
            raise StaleProcurementBuildPlanError(
                f"expected {key} does not match preflight plan"
            )

    if "source_fingerprint" not in expected:
        expected = {
            "source_fingerprint": plan.source_fingerprint,
            "identity_fingerprint": plan.identity_fingerprint,
            "build_plan_fingerprint": plan.build_plan_fingerprint,
            "semantic_plan_digest": plan.semantic_plan_digest,
        }

    applied = apply_procurement_plan(
        sqlite_path=path,
        preflight=plan,
        as_of_date=as_of,
        run_context=ctx,
        expected=expected,
        inject_source_change=inject_source_change,
        inject_identity_change=inject_identity_change,
        inject_after_schema=inject_after_schema,
        inject_after_delete=inject_after_delete,
        inject_mid_signal_insert=inject_mid_signal_insert,
        inject_before_validation=inject_before_validation,
        inject_before_commit=inject_before_commit,
        inject_bad_readback=inject_bad_readback,
        force_foreign_keys_off=force_foreign_keys_off,
    )
    live_plan: ProcurementPlan = applied["plan"]
    summary = plan_to_json_summary(
        live_plan,
        sqlite_path=path,
        generated_at_utc=generated_at,
        snapshot_diagnostics=snapshot_diagnostics,
        mode="apply",
        applied=True,
        extra={
            "materialized_at_utc": applied["materialized_at_utc"],
            "written_rows": applied["written"],
            "readback_rows": applied["validation"]["readback_counts"],
            "readback_semantic_digest": applied["validation"]["readback_semantic_digest"],
            "foreign_key_check_count": applied["validation"]["foreign_key_check_count"],
            "expected_value_match_statuses": applied["expected_match"],
            "stale_plan_recheck_status": applied["stale_plan_recheck_status"],
            "schema_creation_status": applied["schema_status"],
            "identity_snapshot_unchanged": applied["immutability"][
                "identity_snapshot_unchanged"
            ],
            "opportunity_snapshot_unchanged": applied["immutability"][
                "opportunity_snapshot_unchanged"
            ],
            "procurement_sources_unchanged": applied["immutability"][
                "procurement_sources_unchanged"
            ],
            "temp_schema_validation": {"ok": True, "row_counts": temp_counts},
            "known_account_count": len(known),
            "field_plane_conflict_distribution": live_plan.metrics.get(
                "field_plane_conflict_distribution", {}
            ),
        },
    )
    return ProcurementBuildResult(
        sqlite_path=path,
        plan=live_plan,
        summary=summary,
        temp_schema_counts=temp_counts,
    )


def run_procurement_dry_run(
    *,
    sqlite_path: Path,
    as_of_date: date | str,
    run_context: str | None = None,
    apply: bool = False,
    validate_temp_schema: bool = True,
    expected_source_fingerprint: str | None = None,
    expected_identity_fingerprint: str | None = None,
    expected_build_plan_fingerprint: str | None = None,
    expected_semantic_plan_digest: str | None = None,
) -> ProcurementBuildResult:
    """Always read-only. Refuse apply=True; use run_procurement_build for mutation."""
    if apply:
        raise UnsafeInvocationError(
            "run_procurement_dry_run is read-only; use run_procurement_build(..., apply=True) "
            "with production_apply + expected digests, or allow_fixture_apply=True in tests"
        )
    return run_procurement_build(
        sqlite_path=sqlite_path,
        as_of_date=as_of_date,
        run_context=run_context,
        apply=False,
        validate_temp_schema=validate_temp_schema,
        expected_source_fingerprint=expected_source_fingerprint,
        expected_identity_fingerprint=expected_identity_fingerprint,
        expected_build_plan_fingerprint=expected_build_plan_fingerprint,
        expected_semantic_plan_digest=expected_semantic_plan_digest,
    )


__all__ = [
    "ApplyNotImplementedError",
    "CommercialIdentityPathError",
    "IdentityGateError",
    "PersistenceValidationError",
    "PlanValidationError",
    "ProcurementBuildResult",
    "SchemaIncompatibilityError",
    "SourceSchemaError",
    "StaleProcurementBuildPlanError",
    "TempSchemaValidationError",
    "UnsafeInvocationError",
    "apply_procurement_plan",
    "assert_no_write_connection",
    "connect_production_readonly",
    "parse_as_of_date",
    "plan_to_json_summary",
    "require_explicit_sqlite_path",
    "run_procurement_build",
    "run_procurement_dry_run",
]
