"""Orchestrate SQLite → Postgres dashboard mirror sync (read-only SQLite, scratch-safe Postgres)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import urlparse, urlunparse

from origenlab_email_pipeline.classification_postgres_mirror import (
    sync_email_classification_canonical,
)
from origenlab_email_pipeline.commercial_deal_postgres_mirror import (
    sync_commercial_deals,
)
from origenlab_email_pipeline.commercial_opportunity_postgres_mirror import (
    sync_commercial_opportunity_postgres_mirror,
)
from origenlab_email_pipeline.commercial_purchase_postgres_mirror import (
    sync_commercial_purchase_events,
)
from origenlab_email_pipeline.contacto_gmail_source import (
    sql_predicate_contacto_gmail_source,
)
from origenlab_email_pipeline.equipment_opportunity_mirror import (
    apply_load as apply_equipment_opportunity_mirror,
    preview_load as preview_equipment_opportunity_mirror,
)
from origenlab_email_pipeline.mart_core_postgres_migrate import (
    assert_scratch_postgres_target,
    connect_sqlite_readonly,
    resolve_postgres_url,
    resolve_sqlite_path,
)
from origenlab_email_pipeline.dashboard_snapshot_publish import (
    publish_operator_dashboard_snapshots,
)
from origenlab_email_pipeline.warm_case_promotion import (
    apply_promotion as apply_warm_case_promotion,
    preview_promotion as preview_warm_case_promotion,
)

try:
    import psycopg
    from psycopg.types.json import Json
except ImportError as exc:  # pragma: no cover
    psycopg = None  # type: ignore[misc, assignment]
    Json = None  # type: ignore[misc, assignment]
    _PSYCOPG_IMPORT_ERROR = exc
else:
    _PSYCOPG_IMPORT_ERROR = None

EXPECTED_ALEMBIC_HEAD = "20260901_0042"
DASHBOARD_SYNC_KV_KEY = "dashboard_postgres_mirror_last_sync"

OUTBOUND_SCRIPT = "scripts/migrate/sqlite_outbound_sidecars_to_postgres.py"
MART_SCRIPT = "scripts/migrate/sqlite_mart_core_to_postgres.py"

OnlyMode = Literal["outbound", "mart", "canonical"]

REQUIRED_MIRROR_TABLES: tuple[tuple[str, str], ...] = (
    ("outbound", "contact_email_suppression"),
    ("outbound", "contact_domain_suppression"),
    ("outbound", "outreach_contact_state"),
    ("mart", "contact_master"),
    ("mart", "organization_master"),
    ("mart", "opportunity_signals"),
    ("mart", "contact_master_canonical"),
    ("mart", "organization_master_canonical"),
    ("mart", "opportunity_signals_canonical"),
    ("commercial", "opportunity"),
    ("commercial", "opportunity_event"),
    ("commercial", "opportunity_evidence"),
    ("commercial", "opportunity_conflict"),
)

REPORTING_WATERMARK_TABLE: tuple[str, str] = ("reporting", "dashboard_sync_run")

# SQLite mart tables read by mart_core loader (--replace). Guard before any Postgres mart wipe.
REQUIRED_SQLITE_MART_TABLES: tuple[str, ...] = (
    "contact_master",
    "organization_master",
    "opportunity_signals",
)


@dataclass(frozen=True)
class LoaderStep:
    name: str
    script_relpath: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class SqliteMartSourceCounts:
    canonical_gmail_email_count: int
    mart_table_counts: dict[str, int]


def redact_postgres_url(url: str) -> str:
    """Return URL safe for logs/watermarks (password removed)."""
    parsed = urlparse(url.strip())
    if not parsed.scheme:
        return "<invalid-url>"
    netloc = parsed.netloc
    if "@" in netloc:
        userinfo, hostpart = netloc.rsplit("@", 1)
        user = userinfo.split(":", 1)[0] if userinfo else ""
        netloc = f"{user}:***@{hostpart}" if user else f"***@{hostpart}"
    redacted = parsed._replace(netloc=netloc)
    return urlunparse(redacted)


def _require_psycopg() -> None:
    if psycopg is None:
        raise RuntimeError(
            f"psycopg is required (uv sync --group postgres). ({_PSYCOPG_IMPORT_ERROR})"
        )


def phase_log(message: str, *, log: Callable[[str], None] | None = None) -> None:
    sink = log or (lambda m: print(m, flush=True))
    sink(message)


def pg_table_exists(cur: Any, *, schema: str, table: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = %s AND table_name = %s
        LIMIT 1
        """,
        (schema, table),
    )
    return cur.fetchone() is not None


def fetch_alembic_version(cur: Any) -> str | None:
    if not pg_table_exists(cur, schema="public", table="alembic_version"):
        return None
    cur.execute("SELECT version_num FROM alembic_version LIMIT 1")
    row = cur.fetchone()
    if not row:
        return None
    return str(row[0] if not isinstance(row, dict) else row.get("version_num"))


def check_alembic_head(cur: Any) -> tuple[bool, str | None, str]:
    version = fetch_alembic_version(cur)
    if version is None:
        return (
            False,
            None,
            "alembic_version table missing or empty (run: uv run alembic upgrade head)",
        )
    if version != EXPECTED_ALEMBIC_HEAD:
        return (
            False,
            version,
            f"Alembic head mismatch: database={version!r} expected={EXPECTED_ALEMBIC_HEAD!r}",
        )
    return True, version, ""


def list_missing_tables(cur: Any, tables: tuple[tuple[str, str], ...]) -> list[str]:
    missing: list[str] = []
    for schema, table in tables:
        if not pg_table_exists(cur, schema=schema, table=table):
            missing.append(f"{schema}.{table}")
    return missing


def _sqlite_table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return bool(row)


def _sqlite_table_count(conn: sqlite3.Connection, table: str) -> int:
    if not _sqlite_table_exists(conn, table):
        return 0
    row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return int(row[0] if row else 0)


def count_canonical_gmail_emails(conn: sqlite3.Connection) -> int:
    if not _sqlite_table_exists(conn, "emails"):
        return 0
    pred = sql_predicate_contacto_gmail_source(table_alias=None, coalesce_null=False)
    row = conn.execute(f"SELECT COUNT(*) FROM emails WHERE {pred}").fetchone()
    return int(row[0] if row else 0)


def collect_sqlite_mart_source_counts(sqlite_path: Path) -> SqliteMartSourceCounts:
    """Read-only counts from SQLite before mirroring mart tables to Postgres."""
    conn = connect_sqlite_readonly(sqlite_path)
    try:
        canonical = count_canonical_gmail_emails(conn)
        mart_counts = {
            t: _sqlite_table_count(conn, t) for t in REQUIRED_SQLITE_MART_TABLES
        }
    finally:
        conn.close()
    return SqliteMartSourceCounts(
        canonical_gmail_email_count=canonical,
        mart_table_counts=mart_counts,
    )


def assert_sqlite_mart_ready_for_mirror_sync(
    sqlite_path: Path,
    *,
    allow_empty_mart: bool = False,
) -> SqliteMartSourceCounts:
    """Fail closed when canonical Gmail exists but SQLite mart tables are empty."""
    counts = collect_sqlite_mart_source_counts(sqlite_path)
    if allow_empty_mart or counts.canonical_gmail_email_count == 0:
        return counts

    empty_tables = [
        t
        for t in REQUIRED_SQLITE_MART_TABLES
        if counts.mart_table_counts.get(t, 0) == 0
    ]
    if not empty_tables:
        return counts

    listed = ", ".join(empty_tables)
    raise ValueError(
        "Refusing Postgres dashboard mirror sync: SQLite has "
        f"{counts.canonical_gmail_email_count} canonical Gmail email row(s) "
        f"(source_file LIKE 'gmail:contacto@origenlab.cl/%') but mart table(s) "
        f"{listed} are empty. This usually indicates a failed or incomplete "
        "build_business_mart.py --rebuild. Rebuild the mart on SQLite first, or pass "
        "--allow-empty-mart (break-glass only; may replace good Postgres mirror data "
        "with empty mart loads)."
    )


def mart_loader_planned(steps: list[LoaderStep]) -> bool:
    return any(step.name == "mart_core" for step in steps)


def preflight_sqlite(sqlite_path: Path) -> None:
    if not sqlite_path.is_file():
        raise FileNotFoundError(f"SQLite file not found: {sqlite_path}")
    conn = connect_sqlite_readonly(sqlite_path)
    try:
        conn.execute("SELECT 1").fetchone()
    finally:
        conn.close()


def preflight_postgres(pg_url: str) -> tuple[str, list[str]]:
    _require_psycopg()
    assert psycopg is not None
    missing_reporting: list[str] = []
    with psycopg.connect(pg_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            ok, version, msg = check_alembic_head(cur)
            if not ok:
                raise ValueError(msg)
            missing = list_missing_tables(cur, REQUIRED_MIRROR_TABLES)
            if missing:
                raise ValueError(
                    "Postgres mirror tables missing: "
                    + ", ".join(missing)
                    + " (run: uv run alembic -c alembic.ini upgrade head)"
                )
            if not pg_table_exists(
                cur,
                schema=REPORTING_WATERMARK_TABLE[0],
                table=REPORTING_WATERMARK_TABLE[1],
            ):
                missing_reporting.append(
                    f"{REPORTING_WATERMARK_TABLE[0]}.{REPORTING_WATERMARK_TABLE[1]}"
                )
    return version or EXPECTED_ALEMBIC_HEAD, missing_reporting


def collect_mirror_counts(pg_url: str) -> dict[str, int]:
    _require_psycopg()
    assert psycopg is not None
    keys = {
        "canonical_contact_count": "mart.contact_master_canonical",
        "canonical_organization_count": "mart.organization_master_canonical",
        "canonical_opportunity_signal_count": "mart.opportunity_signals_canonical",
        "archive_contact_count": "mart.contact_master",
        "archive_organization_count": "mart.organization_master",
        "archive_opportunity_signal_count": "mart.opportunity_signals",
        "email_suppression_count": "outbound.contact_email_suppression",
        "domain_suppression_count": "outbound.contact_domain_suppression",
        "outreach_state_count": "outbound.outreach_contact_state",
        "commercial_purchase_event_count": "commercial.purchase_event",
        "commercial_purchase_event_item_count": "commercial.purchase_event_item",
        "commercial_deal_count": "commercial.deal",
        "commercial_opportunity_count": "commercial.opportunity",
        "commercial_opportunity_event_count": "commercial.opportunity_event",
        "commercial_opportunity_evidence_count": "commercial.opportunity_evidence",
        "commercial_opportunity_conflict_count": "commercial.opportunity_conflict",
    }
    out: dict[str, int] = {}
    with psycopg.connect(pg_url) as conn:
        with conn.cursor() as cur:
            for key, qualified in keys.items():
                schema, table = qualified.split(".", 1)
                if not pg_table_exists(cur, schema=schema, table=table):
                    out[key] = 0
                    continue
                cur.execute(f"SELECT COUNT(*)::bigint FROM {qualified}")
                row = cur.fetchone()
                out[key] = int(row[0] if not isinstance(row, dict) else row["count"])
    return out


def plan_loader_steps(
    *,
    only: OnlyMode | None,
    skip_outbound: bool,
    skip_mart: bool,
) -> list[LoaderStep]:
    steps: list[LoaderStep] = []
    run_outbound = only == "outbound" or (only is None and not skip_outbound)
    run_mart = only in ("mart", "canonical") or (only is None and not skip_mart)
    if only == "canonical":
        mart_tables = "canonical"
    else:
        mart_tables = "all"
    if run_outbound and only not in ("mart", "canonical"):
        steps.append(
            LoaderStep(
                name="outbound_sidecars",
                script_relpath=OUTBOUND_SCRIPT,
                argv=("--replace",),
            )
        )
    if run_mart and only != "outbound":
        steps.append(
            LoaderStep(
                name="mart_core",
                script_relpath=MART_SCRIPT,
                argv=("--replace", "--tables", mart_tables),
            )
        )
    if not steps:
        raise ValueError("No loader steps selected (check --only / --skip-* flags).")
    return steps


def build_loader_command(
    repo_root: Path,
    step: LoaderStep,
    *,
    sqlite_path: Path,
    postgres_url: str,
    allow_non_scratch: bool,
) -> list[str]:
    script = repo_root / step.script_relpath
    cmd = [
        sys.executable,
        str(script),
        *step.argv,
        "--sqlite-db",
        str(sqlite_path),
        "--postgres-url",
        postgres_url,
    ]
    if allow_non_scratch and step.script_relpath == MART_SCRIPT:
        cmd.append("--allow-non-scratch-postgres")
    return cmd


def run_loader_subprocess(cmd: list[str], *, repo_root: Path) -> int:
    proc = subprocess.run(
        cmd,
        cwd=str(repo_root),
        check=False,
    )
    return int(proc.returncode)


class LifecycleConsistencyError(RuntimeError):
    """Fail-closed lifecycle publication / transition error."""


@dataclass(frozen=True)
class SyncRunHandle:
    """Topology captured at lifecycle start for consistent terminal publication."""

    sync_run_id: int | None
    reporting_enabled: bool
    kv_enabled: bool


def _count_columns(counts: dict[str, int] | None) -> tuple[Any, ...]:
    c = counts or {}
    return (
        c.get("canonical_contact_count"),
        c.get("canonical_organization_count"),
        c.get("canonical_opportunity_signal_count"),
        c.get("archive_contact_count"),
        c.get("archive_organization_count"),
        c.get("archive_opportunity_signal_count"),
        c.get("email_suppression_count"),
        c.get("domain_suppression_count"),
        c.get("outreach_state_count"),
    )


def sanitize_lifecycle_error(
    message: BaseException | str,
    *,
    postgres_url: str | None = None,
    sqlite_path: Path | str | None = None,
) -> str:
    """Sanitize lifecycle/CLI errors without destroying useful classification text."""
    if isinstance(message, BaseException):
        # Prefer the exception message (matches historical str(exc) behavior) so
        # classifications like "Loader mart_core failed..." stay intact.
        text = str(message) or type(message).__name__
    else:
        text = str(message)

    secrets: list[str] = []
    if postgres_url:
        secrets.append(postgres_url)
        secrets.append(redact_postgres_url(postgres_url))
    for key in (
        "ORIGENLAB_POSTGRES_URL",
        "ALEMBIC_DATABASE_URL",
        "ORIGENLAB_CLOUD_POSTGRES_URL",
        "ORIGENLAB_API_AUTH_TOKEN",
    ):
        val = os.environ.get(key)
        if val:
            secrets.append(val)
    if sqlite_path is not None:
        sp = str(sqlite_path)
        secrets.append(sp)
        try:
            secrets.append(str(Path(sqlite_path).resolve()))
        except OSError:
            pass

    # Longest-first so partial overlaps do not leave remnants.
    for secret in sorted({s for s in secrets if s}, key=len, reverse=True):
        if secret and secret in text:
            text = text.replace(secret, "<redacted>")

    # Generic credential-bearing URI forms.
    text = re.sub(
        r"(?i)(postgres(?:ql)?|postgresql\+psycopg(?:2)?)://[^\s\"']+",
        "<redacted-url>",
        text,
    )
    text = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._\-]+", r"\1<redacted-token>", text)
    text = re.sub(r"(?i)(password=)[^\s&;\"']+", r"\1<redacted>", text)
    # Absolute Unix paths → basename-only hint.
    text = re.sub(r"(/[^/\s\"']+)+", lambda m: f"<path:{Path(m.group(0)).name}>", text)
    return text


def _lifecycle_kv_payload(
    *,
    status: str,
    started_at: datetime,
    finished_at: datetime | None,
    sqlite_path: Path,
    postgres_url_redacted: str,
    counts: dict[str, int] | None,
    sync_run_id: int | None,
    details: dict[str, Any],
    error_message: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": status,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat() if finished_at else None,
        "sqlite_path": str(sqlite_path),
        "postgres_url_redacted": postgres_url_redacted,
        "counts": counts or {},
        "sync_run_id": sync_run_id,
        "details": details,
    }
    if error_message is not None:
        payload["error_message"] = error_message
    return payload


def _upsert_dashboard_sync_kv(cur: Any, payload: dict[str, Any]) -> None:
    assert Json is not None
    if not pg_table_exists(cur, schema="ops", table="pipeline_kv"):
        return
    cur.execute(
        """
        INSERT INTO ops.pipeline_kv (kv_key, value_json, updated_at)
        VALUES (%s, %s, now())
        ON CONFLICT (kv_key) DO UPDATE SET
          value_json = EXCLUDED.value_json,
          updated_at = now()
        """,
        (DASHBOARD_SYNC_KV_KEY, Json(payload)),
    )


def _reporting_run_table_exists(cur: Any) -> bool:
    return pg_table_exists(
        cur,
        schema=REPORTING_WATERMARK_TABLE[0],
        table=REPORTING_WATERMARK_TABLE[1],
    )


def _fetch_returning_id(row: Any) -> int | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return int(row["id"])
    return int(row[0])


def _assert_topology_matches_handle(cur: Any, handle: SyncRunHandle) -> None:
    has_run = _reporting_run_table_exists(cur)
    has_kv = pg_table_exists(cur, schema="ops", table="pipeline_kv")
    if handle.reporting_enabled != has_run:
        raise LifecycleConsistencyError(
            "dashboard sync lifecycle table topology changed mid-run "
            f"(reporting_enabled={handle.reporting_enabled}, present={has_run})"
        )
    if handle.kv_enabled != has_kv:
        raise LifecycleConsistencyError(
            "dashboard sync lifecycle table topology changed mid-run "
            f"(kv_enabled={handle.kv_enabled}, present={has_kv})"
        )


def build_selected_loaders(
    steps: list[LoaderStep],
    *,
    include_commercial_deals: bool = False,
    include_commercial_opportunities: bool = False,
    include_equipment_opportunities: bool = False,
    include_warm_cases: bool = False,
    include_operator_snapshots: bool = False,
) -> list[str]:
    """Ordered logical loaders for one non-dry-run invocation."""
    selected = [step.name for step in steps]
    # In-process stages always follow base subprocess loaders on apply.
    selected.extend(["classification", "commercial_purchase"])
    if include_commercial_deals:
        selected.append("commercial_deals")
    if include_commercial_opportunities:
        selected.append("commercial_opportunities")
    if include_equipment_opportunities:
        selected.append("equipment_opportunities")
    if include_warm_cases:
        selected.append("warm_cases")
    if include_operator_snapshots:
        selected.append("operator_snapshots")
    return selected


def build_lifecycle_details(
    *,
    selected_loaders: list[str],
    completed_loaders: list[str],
    loader_steps: list[dict[str, Any]],
    alembic_version: str | None,
    loader_summaries: dict[str, Any] | None = None,
    failed_loader: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    details: dict[str, Any] = {
        "selected_loaders": list(selected_loaders),
        "completed_loaders": list(completed_loaders),
        "loader_steps": list(loader_steps),
        "alembic_version": alembic_version,
        "loader_summaries": dict(loader_summaries or {}),
    }
    if failed_loader is not None:
        details["failed_loader"] = failed_loader
    if extra:
        details.update(extra)
    return details


def start_sync_run(
    pg_url: str,
    *,
    sqlite_path: Path,
    postgres_url_redacted: str,
    started_at: datetime,
    details: dict[str, Any],
    counts: dict[str, int] | None = None,
) -> SyncRunHandle:
    """Insert one ``running`` lifecycle row (and matching KV) before loaders."""
    _require_psycopg()
    assert psycopg is not None and Json is not None
    sync_id: int | None = None
    with psycopg.connect(pg_url, autocommit=False) as conn:
        with conn.cursor() as cur:
            has_run = _reporting_run_table_exists(cur)
            has_kv = pg_table_exists(cur, schema="ops", table="pipeline_kv")
            if not has_run and not has_kv:
                conn.commit()
                return SyncRunHandle(
                    sync_run_id=None, reporting_enabled=False, kv_enabled=False
                )
            if has_run:
                cur.execute(
                    """
                    INSERT INTO reporting.dashboard_sync_run (
                      started_at, finished_at, status,
                      sqlite_path, postgres_url_redacted,
                      canonical_contact_count, canonical_organization_count,
                      canonical_opportunity_signal_count,
                      archive_contact_count, archive_organization_count,
                      archive_opportunity_signal_count,
                      email_suppression_count, domain_suppression_count,
                      outreach_state_count,
                      error_message, details_json
                    ) VALUES (
                      %s, NULL, 'running',
                      %s, %s,
                      %s, %s, %s,
                      %s, %s, %s,
                      %s, %s, %s,
                      NULL, %s
                    )
                    RETURNING id
                    """,
                    (
                        started_at,
                        str(sqlite_path),
                        postgres_url_redacted,
                        *_count_columns(counts),
                        Json(details),
                    ),
                )
                sync_id = _fetch_returning_id(cur.fetchone())
                if sync_id is None:
                    raise LifecycleConsistencyError(
                        "failed to insert running dashboard_sync_run row"
                    )
            if has_kv:
                _upsert_dashboard_sync_kv(
                    cur,
                    _lifecycle_kv_payload(
                        status="running",
                        started_at=started_at,
                        finished_at=None,
                        sqlite_path=sqlite_path,
                        postgres_url_redacted=postgres_url_redacted,
                        counts=counts,
                        sync_run_id=sync_id,
                        details=details,
                    ),
                )
        conn.commit()
    return SyncRunHandle(
        sync_run_id=sync_id,
        reporting_enabled=has_run,
        kv_enabled=has_kv,
    )


def finish_sync_run(
    pg_url: str,
    *,
    handle: SyncRunHandle,
    sqlite_path: Path,
    postgres_url_redacted: str,
    status: Literal["success", "failed"],
    started_at: datetime,
    finished_at: datetime,
    counts: dict[str, int] | None,
    error_message: str | None,
    details: dict[str, Any],
) -> None:
    """Compare-and-set the same running row to a terminal status; publish matching KV."""
    if status not in ("success", "failed"):
        raise ValueError(f"terminal sync status must be success|failed, got {status!r}")
    _require_psycopg()
    assert psycopg is not None and Json is not None

    if handle.reporting_enabled and handle.sync_run_id is None:
        raise LifecycleConsistencyError(
            "reporting lifecycle enabled but sync_run_id is missing; "
            "refusing terminal publication without a durable running row"
        )

    with psycopg.connect(pg_url, autocommit=False) as conn:
        with conn.cursor() as cur:
            _assert_topology_matches_handle(cur, handle)
            sync_run_id = handle.sync_run_id

            if handle.reporting_enabled:
                assert sync_run_id is not None
                cur.execute(
                    """
                    UPDATE reporting.dashboard_sync_run
                    SET finished_at = %s,
                        status = %s,
                        canonical_contact_count = %s,
                        canonical_organization_count = %s,
                        canonical_opportunity_signal_count = %s,
                        archive_contact_count = %s,
                        archive_organization_count = %s,
                        archive_opportunity_signal_count = %s,
                        email_suppression_count = %s,
                        domain_suppression_count = %s,
                        outreach_state_count = %s,
                        error_message = %s,
                        details_json = %s
                    WHERE id = %s
                      AND status = 'running'
                    RETURNING id
                    """,
                    (
                        finished_at,
                        status,
                        *_count_columns(counts),
                        error_message,
                        Json(details),
                        sync_run_id,
                    ),
                )
                returned = _fetch_returning_id(cur.fetchone())
                if returned is None or returned != int(sync_run_id):
                    raise LifecycleConsistencyError(
                        "terminal lifecycle update affected no running row "
                        f"(expected sync_run_id={sync_run_id})"
                    )
                if handle.kv_enabled:
                    _upsert_dashboard_sync_kv(
                        cur,
                        _lifecycle_kv_payload(
                            status=status,
                            started_at=started_at,
                            finished_at=finished_at,
                            sqlite_path=sqlite_path,
                            postgres_url_redacted=postgres_url_redacted,
                            counts=counts,
                            sync_run_id=sync_run_id,
                            details=details,
                            error_message=error_message,
                        ),
                    )
            elif handle.kv_enabled:
                # Explicit KV-only mode: reporting must still be absent.
                _upsert_dashboard_sync_kv(
                    cur,
                    _lifecycle_kv_payload(
                        status=status,
                        started_at=started_at,
                        finished_at=finished_at,
                        sqlite_path=sqlite_path,
                        postgres_url_redacted=postgres_url_redacted,
                        counts=counts,
                        sync_run_id=None,
                        details=details,
                        error_message=error_message,
                    ),
                )
            # Neither table: intentionally no-op.
        conn.commit()


def update_sync_run_details(
    pg_url: str,
    handle: SyncRunHandle,
    details: dict[str, Any],
    *,
    started_at: datetime | None = None,
    sqlite_path: Path | None = None,
    postgres_url_redacted: str | None = None,
    counts: dict[str, int] | None = None,
) -> None:
    """Refresh in-progress details only while the lifecycle row remains ``running``."""
    _require_psycopg()
    assert psycopg is not None and Json is not None
    with psycopg.connect(pg_url, autocommit=False) as conn:
        with conn.cursor() as cur:
            _assert_topology_matches_handle(cur, handle)
            if handle.reporting_enabled:
                if handle.sync_run_id is None:
                    raise LifecycleConsistencyError(
                        "reporting lifecycle enabled but sync_run_id is missing"
                    )
                cur.execute(
                    """
                    UPDATE reporting.dashboard_sync_run
                    SET details_json = %s
                    WHERE id = %s
                      AND status = 'running'
                    RETURNING id
                    """,
                    (Json(details), handle.sync_run_id),
                )
                returned = _fetch_returning_id(cur.fetchone())
                if returned is None or returned != int(handle.sync_run_id):
                    raise LifecycleConsistencyError(
                        "mid-run details update affected no running row "
                        f"(expected sync_run_id={handle.sync_run_id})"
                    )
                if (
                    handle.kv_enabled
                    and started_at is not None
                    and sqlite_path is not None
                    and postgres_url_redacted is not None
                ):
                    _upsert_dashboard_sync_kv(
                        cur,
                        _lifecycle_kv_payload(
                            status="running",
                            started_at=started_at,
                            finished_at=None,
                            sqlite_path=sqlite_path,
                            postgres_url_redacted=postgres_url_redacted,
                            counts=counts,
                            sync_run_id=handle.sync_run_id,
                            details=details,
                        ),
                    )
            elif handle.kv_enabled:
                if (
                    started_at is None
                    or sqlite_path is None
                    or postgres_url_redacted is None
                ):
                    raise LifecycleConsistencyError(
                        "KV-only mid-run update requires started_at, sqlite_path, "
                        "and postgres_url_redacted"
                    )
                _upsert_dashboard_sync_kv(
                    cur,
                    _lifecycle_kv_payload(
                        status="running",
                        started_at=started_at,
                        finished_at=None,
                        sqlite_path=sqlite_path,
                        postgres_url_redacted=postgres_url_redacted,
                        counts=counts,
                        sync_run_id=None,
                        details=details,
                    ),
                )
        conn.commit()


def write_sync_watermark(
    pg_url: str,
    *,
    sqlite_path: Path,
    postgres_url_redacted: str,
    status: str,
    started_at: datetime,
    finished_at: datetime | None,
    counts: dict[str, int],
    error_message: str | None,
    details: dict[str, Any],
    dry_run: bool,
) -> int | None:
    """Compatibility wrapper.

    Prefer :func:`start_sync_run` / :func:`finish_sync_run`. This helper must not
    be used to publish intermediate ``success`` milestones.
    """
    if dry_run:
        return None
    if status == "running":
        return start_sync_run(
            pg_url,
            sqlite_path=sqlite_path,
            postgres_url_redacted=postgres_url_redacted,
            started_at=started_at,
            details=details,
            counts=counts,
        ).sync_run_id
    if status not in ("success", "failed"):
        raise ValueError(f"unsupported watermark status: {status!r}")
    if finished_at is None:
        raise ValueError("finished_at is required for terminal watermark status")
    handle = start_sync_run(
        pg_url,
        sqlite_path=sqlite_path,
        postgres_url_redacted=postgres_url_redacted,
        started_at=started_at,
        details=details,
        counts=counts,
    )
    finish_sync_run(
        pg_url,
        handle=handle,
        sqlite_path=sqlite_path,
        postgres_url_redacted=postgres_url_redacted,
        status=status,  # type: ignore[arg-type]
        started_at=started_at,
        finished_at=finished_at,
        counts=counts,
        error_message=error_message,
        details=details,
    )
    return handle.sync_run_id


def default_active_current_dir(repo_root: Path) -> Path:
    return (repo_root / "reports" / "out" / "active" / "current").resolve()


def validate_optional_loader_audit(
    *,
    include_equipment: bool,
    include_warm_cases: bool,
    dry_run: bool,
    updated_by: str | None,
    reason: str | None,
) -> None:
    if dry_run or not (include_equipment or include_warm_cases):
        return
    if not (updated_by and str(updated_by).strip()):
        raise ValueError(
            "Optional DB-2 loaders on apply require --updated-by (or --operator)."
        )
    if not (reason and str(reason).strip()):
        raise ValueError("Optional DB-2 loaders on apply require --reason.")


def _raise_if_optional_loader_failed(loader_name: str, summary: dict[str, Any]) -> None:
    if summary.get("dry_run"):
        return
    error = summary.get("error")
    if error:
        raise RuntimeError(f"{loader_name} failed: {error}")
    if not summary.get("applied"):
        warning = summary.get("warning")
        if loader_name == "warm_case_promotion" and warning == "no_candidates":
            return
        if loader_name == "equipment_opportunity_mirror" and warning == "empty_csv":
            return
        if (
            loader_name == "equipment_opportunity_mirror"
            and summary.get("idempotent") == "canonical_source_already_loaded"
            and summary.get("is_canonical") is True
        ):
            return
        raise RuntimeError(f"{loader_name} apply did not complete: {summary!r}")


def merge_optional_loader_details(
    details: dict[str, Any],
    *,
    equipment_summary: dict[str, Any] | None,
    warm_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(details)
    if equipment_summary is not None:
        merged["equipment_opportunity_sync"] = equipment_summary
        if equipment_summary.get("source_id") is not None:
            merged["equipment_opportunity_source_id"] = equipment_summary["source_id"]
        row_count = equipment_summary.get("rows_inserted")
        if row_count is None:
            row_count = equipment_summary.get("row_count")
        if row_count is not None:
            merged["equipment_opportunity_row_count"] = row_count
    if warm_summary is not None:
        merged["warm_case_sync"] = warm_summary
        if warm_summary.get("inserted_cases") is not None:
            merged["warm_case_inserted_count"] = warm_summary["inserted_cases"]
        if warm_summary.get("updated_cases") is not None:
            merged["warm_case_updated_count"] = warm_summary["updated_cases"]
        linked = warm_summary.get("linked_emails")
        if linked is not None:
            merged["warm_case_linked_email_count"] = linked
    return merged


def run_equipment_opportunity_sync(
    pg_url: str,
    repo_root: Path,
    *,
    dry_run: bool,
    updated_by: str | None,
    reason: str | None,
    sync_run_id: int | None,
) -> dict[str, Any]:
    active_current = default_active_current_dir(repo_root)
    if dry_run:
        return preview_equipment_opportunity_mirror(
            active_current,
            pg_url=pg_url,
        )
    return apply_equipment_opportunity_mirror(
        pg_url,
        active_current,
        updated_by=str(updated_by or "").strip(),
        reason=str(reason or "").strip(),
        sync_run_id=sync_run_id,
    )


def run_warm_case_promotion_sync(
    pg_url: str,
    sqlite_path: Path,
    *,
    dry_run: bool,
    updated_by: str | None,
    reason: str | None,
    days_window: int = 30,
    limit: int = 200,
    close_missing: bool = False,
) -> dict[str, Any]:
    if dry_run:
        summary = preview_warm_case_promotion(
            sqlite_path,
            days_window=days_window,
            limit=limit,
            pg_url=pg_url,
        )
    else:
        summary = apply_warm_case_promotion(
            pg_url,
            sqlite_path,
            days_window=days_window,
            limit=limit,
            updated_by=str(updated_by or "").strip(),
            reason=str(reason or "").strip(),
            close_missing=close_missing,
        )
    summary["warm_days"] = days_window
    summary["warm_limit"] = limit
    summary["close_missing"] = close_missing
    return summary


def run_optional_db2_loaders(
    *,
    args: argparse.Namespace,
    pg_url: str,
    sqlite_path: Path,
    repo_root: Path,
    sync_run_id: int | None,
    dry_run: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    equipment_summary: dict[str, Any] | None = None
    warm_summary: dict[str, Any] | None = None

    if args.include_equipment_opportunities:
        equipment_summary = run_equipment_opportunity_sync(
            pg_url,
            repo_root,
            dry_run=dry_run,
            updated_by=args.updated_by,
            reason=args.reason,
            sync_run_id=sync_run_id,
        )
        _raise_if_optional_loader_failed(
            "equipment_opportunity_mirror", equipment_summary
        )

    if args.include_warm_cases:
        warm_summary = run_warm_case_promotion_sync(
            pg_url,
            sqlite_path,
            dry_run=dry_run,
            updated_by=args.updated_by,
            reason=args.reason,
            days_window=int(args.warm_days),
            limit=int(args.warm_limit),
            close_missing=bool(args.close_missing_warm_cases),
        )
        _raise_if_optional_loader_failed("warm_case_promotion", warm_summary)

    return equipment_summary, warm_summary


_OPTIONAL_LOADER_ROW_KEYS = (
    "rows_inserted",
    "row_count",
    "inserted_cases",
    "updated_cases",
    "linked_emails",
    "source_id",
    "skipped",
    "candidate_count",
    "queue_row_count",
    "warm_days",
    "warm_limit",
    "close_missing",
    "closed_missing_cases",
    "reopened_cases",
)


def _format_optional_loader_summary(name: str, summary: dict[str, Any]) -> list[str]:
    lines = [f"    {name}:"]
    for key in ("dry_run", "applied", "warning", "error"):
        if key in summary and summary[key] is not None:
            lines.append(f"      {key}: {summary[key]}")
    for key in _OPTIONAL_LOADER_ROW_KEYS:
        if key in summary and summary[key] is not None:
            lines.append(f"      {key}: {summary[key]}")
    categories = summary.get("categories_summary")
    if isinstance(categories, dict) and categories:
        compact = ", ".join(
            f"{cat}={count}" for cat, count in sorted(categories.items())
        )
        lines.append(f"      categories_summary: {compact}")
    return lines


def format_summary_text(result: dict[str, Any]) -> str:
    c = result.get("counts") or {}
    lines = [
        "[sync] dashboard Postgres mirror",
        f"  status: {result.get('status')}",
        f"  dry_run: {result.get('dry_run')}",
        f"  elapsed_seconds: {result.get('elapsed_seconds')}",
        "  canonical:",
        f"    contacts: {c.get('canonical_contact_count')}",
        f"    organizations: {c.get('canonical_organization_count')}",
        f"    opportunity_signals: {c.get('canonical_opportunity_signal_count')}",
        "  archive:",
        f"    contacts: {c.get('archive_contact_count')}",
        f"    organizations: {c.get('archive_organization_count')}",
        f"    opportunity_signals: {c.get('archive_opportunity_signal_count')}",
        "  outbound:",
        f"    email_suppressions: {c.get('email_suppression_count')}",
        f"    domain_suppressions: {c.get('domain_suppression_count')}",
        f"    outreach_state: {c.get('outreach_state_count')}",
        "  commercial:",
        f"    purchase_events: {c.get('commercial_purchase_event_count')}",
        f"    purchase_event_items: {c.get('commercial_purchase_event_item_count')}",
        f"    deals: {c.get('commercial_deal_count')}",
        f"    opportunities: {c.get('commercial_opportunity_count')}",
        f"    opportunity_events: {c.get('commercial_opportunity_event_count')}",
        f"    opportunity_evidence: {c.get('commercial_opportunity_evidence_count')}",
        f"    opportunity_conflicts: {c.get('commercial_opportunity_conflict_count')}",
    ]
    if result.get("sync_run_id") is not None:
        lines.append(f"  sync_run_id: {result.get('sync_run_id')}")
    if result.get("errors"):
        lines.append(f"  errors: {result['errors']}")
    optional_loaders = (
        ("equipment_opportunities", "equipment_opportunity_sync"),
        ("warm_cases", "warm_case_sync"),
        ("commercial_deals", "commercial_deals_sync"),
        ("commercial_opportunities", "commercial_opportunity_sync"),
    )
    present_loaders = [
        (label, result[key])
        for label, key in optional_loaders
        if isinstance(result.get(key), dict)
    ]
    if present_loaders:
        lines.append("  optional dashboard loaders:")
        for label, summary in present_loaders:
            lines.extend(_format_optional_loader_summary(label, summary))
    lines.extend(
        [
            "",
            "API smoke (apps/api :8001 mirror — preferred; after uvicorn + ORIGENLAB_POSTGRES_URL):",
            "  curl -sS 'http://127.0.0.1:8001/mirror/dashboard/summary' | uv run python -m json.tool",
            "  curl -sS 'http://127.0.0.1:8001/mirror/dashboard/summary?scope=archive' | uv run python -m json.tool",
            "  curl -sS 'http://127.0.0.1:8001/mirror/meta/dashboard-sync' | uv run python -m json.tool",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Refresh Postgres dashboard mirror from SQLite (read-only). "
            "Runs outbound sidecars then mart core loaders."
        )
    )
    p.add_argument("--sqlite-db", type=Path, default=None)
    p.add_argument("--postgres-url", default=None)
    p.add_argument(
        "--dry-run", action="store_true", help="Preflight only; no loader writes"
    )
    p.add_argument("--only", choices=("outbound", "mart", "canonical"), default=None)
    p.add_argument("--skip-outbound", action="store_true")
    p.add_argument("--skip-mart", action="store_true")
    p.add_argument("--json-out", type=Path, default=None)
    p.add_argument(
        "--allow-non-scratch-postgres",
        action="store_true",
        help="Pass through to mart loader when target is not scratch/staging",
    )
    p.add_argument(
        "--allow-empty-mart",
        action="store_true",
        help=(
            "Break-glass: skip SQLite mart empty check before --replace loaders. "
            "May wipe good Postgres dashboard mart data if mart rebuild failed."
        ),
    )
    p.add_argument(
        "--include-equipment-opportunities",
        action="store_true",
        help="After mirror loaders, run equipment_first_operator_queue Postgres mirror (DB-2A).",
    )
    p.add_argument(
        "--include-warm-cases",
        action="store_true",
        help="After mirror loaders, promote SQLite warm review queue to Postgres (DB-2B).",
    )
    p.add_argument(
        "--include-commercial-deals",
        action="store_true",
        help=(
            "After purchase_events sync, mirror redacted commercial_deal ledger "
            "to commercial.deal (opt-in; requires Alembic 20260526_0018)."
        ),
    )
    p.add_argument(
        "--include-commercial-opportunities",
        action="store_true",
        help=(
            "Mirror the validated SQLite PR3 commercial opportunity lifecycle "
            "to commercial.opportunity* "
            "(opt-in; requires Alembic 20260826_0036)."
        ),
    )
    p.add_argument(
        "--dashboard-fast",
        action="store_true",
        help=(
            "Fast daily mirror mode: canonical mart loader only (no archive mart replace). "
            "Does not change default behavior unless explicitly set."
        ),
    )
    p.add_argument(
        "--updated-by",
        "--operator",
        dest="updated_by",
        default=None,
        help="Operator id for optional DB-2 loaders (required with --apply when flags set).",
    )
    p.add_argument(
        "--reason",
        default=None,
        help="Audit reason for optional DB-2 loaders (required with --apply when flags set).",
    )
    p.add_argument(
        "--warm-days",
        type=int,
        default=30,
        help="Warm-case promotion lookback window in days (default: 30).",
    )
    p.add_argument(
        "--warm-limit",
        type=int,
        default=200,
        help="Warm-case promotion SQLite queue row limit (default: 200).",
    )
    p.add_argument(
        "--close-missing-warm-cases",
        action="store_true",
        help=(
            "Close promoted warm_queue_promotion cases absent from the current "
            "candidate snapshot."
        ),
    )
    p.add_argument(
        "--include-operator-snapshots",
        action="store_true",
        help=(
            "Build Gmail interaction audit + operator automation status snapshots "
            "and publish to ops.pipeline_kv on apply (read-only evidence)."
        ),
    )
    return p


def run_operator_snapshots_sync(
    *,
    pg_url: str,
    sqlite_path: Path,
    repo_root: Path,
    dry_run: bool,
) -> dict[str, Any]:
    active_current = default_active_current_dir(repo_root)
    return publish_operator_dashboard_snapshots(
        pg_url,
        sqlite_path=sqlite_path,
        active_current_dir=active_current,
        dry_run=dry_run,
    )


def run_dashboard_mirror_sync(
    argv: list[str] | None = None,
    *,
    repo_root: Path | None = None,
    loader_runner: Callable[[list[str], Path], int] | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    args = build_parser().parse_args(argv)
    root = repo_root or Path(__file__).resolve().parents[2]

    result: dict[str, Any] = {
        "ok": False,
        "dry_run": bool(args.dry_run),
        "status": "failed",
        "sqlite_path": "",
        "postgres_url_redacted": "",
        "alembic_version": None,
        "loader_steps": [],
        "counts": {},
        "sync_run_id": None,
        "errors": [],
        "warnings": [],
        "elapsed_seconds": 0.0,
        "timings": {},
    }

    t0 = time.monotonic()
    started_at = datetime.now(timezone.utc)

    try:
        if args.dashboard_fast and args.only is None:
            args.only = "canonical"
        sqlite_path = resolve_sqlite_path(args.sqlite_db)
        pg_url = resolve_postgres_url(args.postgres_url)
        assert_scratch_postgres_target(
            pg_url, allow_non_scratch=bool(args.allow_non_scratch_postgres)
        )
        validate_optional_loader_audit(
            include_equipment=bool(args.include_equipment_opportunities),
            include_warm_cases=bool(args.include_warm_cases),
            dry_run=bool(args.dry_run),
            updated_by=args.updated_by,
            reason=args.reason,
        )
    except (ValueError, RuntimeError) as exc:
        result["errors"].append(str(exc))
        result["elapsed_seconds"] = round(time.monotonic() - t0, 3)
        return result

    result["sqlite_path"] = str(sqlite_path)
    result["postgres_url_redacted"] = redact_postgres_url(pg_url)

    phase_log("[sync] preflight SQLite (read-only)...", log=log)
    try:
        preflight_sqlite(sqlite_path)
    except (FileNotFoundError, sqlite3.Error) as exc:
        result["errors"].append(str(exc))
        result["elapsed_seconds"] = round(time.monotonic() - t0, 3)
        return result

    phase_log("[sync] preflight Postgres...", log=log)
    try:
        alembic_version, reporting_missing = preflight_postgres(pg_url)
        result["alembic_version"] = alembic_version
        if reporting_missing:
            result["warnings"].append(
                "reporting.dashboard_sync_run not present; sync run row skipped "
                "(run alembic upgrade head). ops.pipeline_kv lifecycle may still update."
            )
    except ValueError as exc:
        result["errors"].append(str(exc))
        result["elapsed_seconds"] = round(time.monotonic() - t0, 3)
        return result

    try:
        steps = plan_loader_steps(
            only=args.only,
            skip_outbound=bool(args.skip_outbound),
            skip_mart=bool(args.skip_mart),
        )
    except ValueError as exc:
        result["errors"].append(str(exc))
        result["elapsed_seconds"] = round(time.monotonic() - t0, 3)
        return result

    result["loader_steps"] = [
        {"name": s.name, "script": s.script_relpath, "argv": list(s.argv)}
        for s in steps
    ]

    if mart_loader_planned(steps):
        phase_log("[sync] checking SQLite mart source counts (read-only)...", log=log)
        try:
            source_counts = assert_sqlite_mart_ready_for_mirror_sync(
                sqlite_path,
                allow_empty_mart=bool(args.allow_empty_mart),
            )
            result["sqlite_source_counts"] = {
                "canonical_gmail_email_count": source_counts.canonical_gmail_email_count,
                **source_counts.mart_table_counts,
            }
        except ValueError as exc:
            result["errors"].append(str(exc))
            result["elapsed_seconds"] = round(time.monotonic() - t0, 3)
            return result

    if args.dry_run:
        result["counts"] = collect_mirror_counts(pg_url)

        commercial_opportunity_summary: dict[str, Any] | None = None
        if args.include_commercial_opportunities:
            try:
                commercial_opportunity_summary = (
                    sync_commercial_opportunity_postgres_mirror(
                        pg_url,
                        sqlite_path,
                        dry_run=True,
                    )
                )
            except RuntimeError as exc:
                result["errors"].append(str(exc))
                result["elapsed_seconds"] = round(
                    time.monotonic() - t0,
                    3,
                )
                return result

            result["commercial_opportunity_sync"] = commercial_opportunity_summary

        equipment_summary: dict[str, Any] | None = None
        warm_summary: dict[str, Any] | None = None
        if args.include_equipment_opportunities or args.include_warm_cases:
            try:
                equipment_summary, warm_summary = run_optional_db2_loaders(
                    args=args,
                    pg_url=pg_url,
                    sqlite_path=sqlite_path,
                    repo_root=root,
                    sync_run_id=None,
                    dry_run=True,
                )
            except RuntimeError as exc:
                result["errors"].append(str(exc))
                result["elapsed_seconds"] = round(time.monotonic() - t0, 3)
                return result
        if equipment_summary is not None:
            result["equipment_opportunity_sync"] = equipment_summary
        if warm_summary is not None:
            result["warm_case_sync"] = warm_summary
        operator_snapshots: dict[str, Any] | None = None
        if args.include_operator_snapshots:
            operator_snapshots = run_operator_snapshots_sync(
                pg_url=pg_url,
                sqlite_path=sqlite_path,
                repo_root=root,
                dry_run=True,
            )
            result["operator_snapshots"] = operator_snapshots
        result["details"] = merge_optional_loader_details(
            {
                "loader_steps": result["loader_steps"],
                "alembic_version": alembic_version,
            },
            equipment_summary=equipment_summary,
            warm_summary=warm_summary,
        )
        if commercial_opportunity_summary is not None:
            result["details"]["commercial_opportunity_sync"] = (
                commercial_opportunity_summary
            )
        if operator_snapshots is not None:
            result["details"]["operator_snapshots"] = operator_snapshots
        result["ok"] = True
        result["status"] = "dry_run"
        result["elapsed_seconds"] = round(time.monotonic() - t0, 3)
        phase_log(
            "[sync] dry-run ok (no loaders executed, Postgres mirror unchanged)",
            log=log,
        )
        return result

    selected_loaders = build_selected_loaders(
        steps,
        include_commercial_deals=bool(args.include_commercial_deals),
        include_commercial_opportunities=bool(args.include_commercial_opportunities),
        include_equipment_opportunities=bool(args.include_equipment_opportunities),
        include_warm_cases=bool(args.include_warm_cases),
        include_operator_snapshots=bool(args.include_operator_snapshots),
    )
    completed_loaders: list[str] = []
    failed_loader: str | None = None
    loader_summaries: dict[str, Any] = {}
    lifecycle_handle = SyncRunHandle(
        sync_run_id=None, reporting_enabled=False, kv_enabled=False
    )
    lifecycle_started = False

    def _sanitize(msg: BaseException | str) -> str:
        return sanitize_lifecycle_error(
            msg, postgres_url=pg_url, sqlite_path=sqlite_path
        )

    running_details = build_lifecycle_details(
        selected_loaders=selected_loaders,
        completed_loaders=completed_loaders,
        loader_steps=result["loader_steps"],
        alembic_version=alembic_version,
    )
    try:
        lifecycle_handle = start_sync_run(
            pg_url,
            sqlite_path=sqlite_path,
            postgres_url_redacted=result["postgres_url_redacted"],
            started_at=started_at,
            details=running_details,
            counts=None,
        )
        lifecycle_started = True
    except Exception as exc:  # noqa: BLE001
        result["errors"].append(
            "Failed to publish running sync lifecycle before loaders: " + _sanitize(exc)
        )
        result["status"] = "failed"
        result["elapsed_seconds"] = round(time.monotonic() - t0, 3)
        return result

    sync_id = lifecycle_handle.sync_run_id
    result["sync_run_id"] = sync_id
    result["selected_loaders"] = list(selected_loaders)

    def _mark_completed(name: str, summary: Any | None = None) -> None:
        if name not in completed_loaders:
            completed_loaders.append(name)
        if summary is not None:
            loader_summaries[name] = summary

    def _current_details(*, failed: str | None = None) -> dict[str, Any]:
        extra: dict[str, Any] = {}
        for key, value in loader_summaries.items():
            # Preserve prior result field names for known in-process stages.
            if key == "classification":
                extra["classification_sync"] = value
            elif key == "commercial_purchase":
                extra["commercial_purchase_sync"] = value
            elif key == "commercial_deals":
                extra["commercial_deals_sync"] = value
            elif key == "commercial_opportunities":
                extra["commercial_opportunity_sync"] = value
            elif key == "equipment_opportunities":
                extra = merge_optional_loader_details(
                    extra, equipment_summary=value, warm_summary=None
                )
            elif key == "warm_cases":
                extra = merge_optional_loader_details(
                    extra, equipment_summary=None, warm_summary=value
                )
            elif key == "operator_snapshots":
                extra["operator_snapshots"] = value
        return build_lifecycle_details(
            selected_loaders=selected_loaders,
            completed_loaders=completed_loaders,
            loader_steps=result["loader_steps"],
            alembic_version=alembic_version,
            loader_summaries=loader_summaries,
            failed_loader=failed,
            extra=extra,
        )

    def _publish_terminal(
        *,
        status: Literal["success", "failed"],
        error_message: str | None,
        failed: str | None,
    ) -> None:
        finish_sync_run(
            pg_url,
            handle=lifecycle_handle,
            sqlite_path=sqlite_path,
            postgres_url_redacted=result["postgres_url_redacted"],
            status=status,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            counts=result.get("counts") or {},
            error_message=error_message,
            details=_current_details(failed=failed),
        )

    def _maybe_update_details() -> None:
        if not (lifecycle_handle.reporting_enabled or lifecycle_handle.kv_enabled):
            return
        update_sync_run_details(
            pg_url,
            lifecycle_handle,
            _current_details(),
            started_at=started_at,
            sqlite_path=sqlite_path,
            postgres_url_redacted=result["postgres_url_redacted"],
            counts=result.get("counts") or {},
        )

    try:
        for step in steps:
            failed_loader = step.name
            cmd = build_loader_command(
                root,
                step,
                sqlite_path=sqlite_path,
                postgres_url=pg_url,
                allow_non_scratch=bool(args.allow_non_scratch_postgres),
            )
            phase_log(f"[sync] loader {step.name}: {' '.join(cmd[2:6])} ...", log=log)
            step_t0 = time.monotonic()
            if loader_runner is not None:
                rc = loader_runner(cmd, root)
            else:
                rc = run_loader_subprocess(cmd, repo_root=root)
            result.setdefault("timings", {})[f"loader_{step.name}_seconds"] = round(
                time.monotonic() - step_t0, 3
            )
            phase_log(
                f"[sync] loader {step.name} finished in "
                f"{result['timings'][f'loader_{step.name}_seconds']}s",
                log=log,
            )
            if rc != 0:
                raise RuntimeError(f"Loader {step.name} failed with exit code {rc}")
            _mark_completed(step.name)
            failed_loader = None

        failed_loader = "classification"
        stage_t0 = time.monotonic()
        classification_sync = sync_email_classification_canonical(
            pg_url,
            sqlite_path,
            sync_run_id=sync_id,
            dry_run=False,
        )
        result.setdefault("timings", {})["classification_sync_seconds"] = round(
            time.monotonic() - stage_t0, 3
        )
        result["classification_sync"] = classification_sync
        _mark_completed("classification", classification_sync)
        failed_loader = None

        failed_loader = "commercial_purchase"
        stage_t0 = time.monotonic()
        purchase_sync = sync_commercial_purchase_events(
            pg_url,
            sqlite_path,
            sync_run_id=sync_id,
            dry_run=False,
        )
        result.setdefault("timings", {})["commercial_purchase_sync_seconds"] = round(
            time.monotonic() - stage_t0, 3
        )
        result["commercial_purchase_sync"] = purchase_sync
        _mark_completed("commercial_purchase", purchase_sync)
        failed_loader = None

        if args.include_commercial_deals:
            failed_loader = "commercial_deals"
            stage_t0 = time.monotonic()
            deals_sync = sync_commercial_deals(
                pg_url,
                sqlite_path,
                sync_run_id=sync_id,
                dry_run=False,
            )
            result.setdefault("timings", {})["commercial_deals_sync_seconds"] = round(
                time.monotonic() - stage_t0, 3
            )
            result["commercial_deals_sync"] = deals_sync
            _mark_completed("commercial_deals", deals_sync)
            failed_loader = None

        if args.include_commercial_opportunities:
            failed_loader = "commercial_opportunities"
            stage_t0 = time.monotonic()

            commercial_opportunity_sync = sync_commercial_opportunity_postgres_mirror(
                pg_url,
                sqlite_path,
                dry_run=False,
            )

            result.setdefault("timings", {})["commercial_opportunity_sync_seconds"] = (
                round(time.monotonic() - stage_t0, 3)
            )

            result["commercial_opportunity_sync"] = commercial_opportunity_sync
            _mark_completed(
                "commercial_opportunities",
                commercial_opportunity_sync,
            )
            failed_loader = None
            _maybe_update_details()

        if args.include_equipment_opportunities or args.include_warm_cases:
            stage_t0 = time.monotonic()
            try:
                if args.include_equipment_opportunities:
                    failed_loader = "equipment_opportunities"
                    equipment_summary = run_equipment_opportunity_sync(
                        pg_url,
                        root,
                        dry_run=False,
                        updated_by=args.updated_by,
                        reason=args.reason,
                        sync_run_id=sync_id,
                    )
                    _raise_if_optional_loader_failed(
                        "equipment_opportunity_mirror", equipment_summary
                    )
                    result["equipment_opportunity_sync"] = equipment_summary
                    _mark_completed("equipment_opportunities", equipment_summary)
                    failed_loader = None
                if args.include_warm_cases:
                    failed_loader = "warm_cases"
                    warm_summary = run_warm_case_promotion_sync(
                        pg_url,
                        sqlite_path,
                        dry_run=False,
                        updated_by=args.updated_by,
                        reason=args.reason,
                        days_window=int(args.warm_days),
                        limit=int(args.warm_limit),
                        close_missing=bool(args.close_missing_warm_cases),
                    )
                    _raise_if_optional_loader_failed(
                        "warm_case_promotion", warm_summary
                    )
                    result["warm_case_sync"] = warm_summary
                    _mark_completed("warm_cases", warm_summary)
                    failed_loader = None
            finally:
                result.setdefault("timings", {})["optional_db2_loaders_seconds"] = (
                    round(time.monotonic() - stage_t0, 3)
                )
            if (
                "equipment_opportunities" in completed_loaders
                or "warm_cases" in completed_loaders
            ):
                _maybe_update_details()

        if args.include_operator_snapshots:
            failed_loader = "operator_snapshots"
            stage_t0 = time.monotonic()
            operator_snapshots = run_operator_snapshots_sync(
                pg_url=pg_url,
                sqlite_path=sqlite_path,
                repo_root=root,
                dry_run=False,
            )
            result.setdefault("timings", {})["operator_snapshots_seconds"] = round(
                time.monotonic() - stage_t0, 3
            )
            result["operator_snapshots"] = operator_snapshots
            _mark_completed("operator_snapshots", operator_snapshots)
            failed_loader = None
            _maybe_update_details()

        failed_loader = "final_counts"
        result["counts"] = collect_mirror_counts(pg_url)
        failed_loader = None
        result["details"] = _current_details()
        result["completed_loaders"] = list(completed_loaders)
        try:
            _publish_terminal(status="success", error_message=None, failed=None)
        except Exception as pub_exc:  # noqa: BLE001
            result["errors"].append(
                "Failed to publish terminal success lifecycle: " + _sanitize(pub_exc)
            )
            result["status"] = "failed"
            result["ok"] = False
            result["elapsed_seconds"] = round(time.monotonic() - t0, 3)
            return result
        result["ok"] = True
        result["status"] = "success"
    except Exception as exc:  # noqa: BLE001
        sanitized = _sanitize(exc)
        result["errors"].append(sanitized)
        result["status"] = "failed"
        result["ok"] = False
        result["completed_loaders"] = list(completed_loaders)
        try:
            result["counts"] = collect_mirror_counts(pg_url)
        except Exception:  # noqa: BLE001
            pass
        if lifecycle_started:
            try:
                _publish_terminal(
                    status="failed",
                    error_message=sanitized,
                    failed=failed_loader,
                )
            except Exception as pub_exc:  # noqa: BLE001
                result["errors"].append(
                    "Failed to publish terminal failure lifecycle: "
                    + _sanitize(pub_exc)
                )
        result["details"] = _current_details(failed=failed_loader)

    result["elapsed_seconds"] = round(time.monotonic() - t0, 3)
    return result


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_dashboard_mirror_sync(
        argv,
        loader_runner=None,
    )
    if args.json_out:
        args.json_out.write_text(
            json.dumps(result, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
    print(format_summary_text(result))
    if result.get("errors"):
        for err in result["errors"]:
            print(err, file=sys.stderr)
    return 0 if result.get("ok") else 1
