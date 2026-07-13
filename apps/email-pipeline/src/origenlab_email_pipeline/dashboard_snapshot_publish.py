"""Publish read-only dashboard snapshots to Postgres ops.pipeline_kv."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.dashboard_gmail_interaction_audit import (
    build_gmail_interaction_audit_snapshot,
)
from origenlab_email_pipeline.operator_cli.operator_automation_status import (
    OperatorAutomationStatusOptions,
    build_operator_automation_status,
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

GMAIL_INTERACTION_AUDIT_KV_KEY = "dashboard_gmail_interaction_audit_v1"
OPERATOR_AUTOMATION_STATUS_SNAPSHOT_KV_KEY = "operator_automation_status_snapshot_v1"

ACTIVE_CURRENT_REDACTION = "<local-active-current>"
_PATH_LIKE = re.compile(r"(^[/~])|([/\\][\w.-]+){2,}")


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


def _require_psycopg() -> None:
    if psycopg is None:
        raise RuntimeError(
            f"psycopg is required (uv sync --group postgres). ({_PSYCOPG_IMPORT_ERROR})"
        )


def _redact_string(value: str) -> str:
    text = value.strip()
    if not text:
        return text
    if _PATH_LIKE.search(text) or text.endswith(".sqlite") or "active/current" in text:
        return ACTIVE_CURRENT_REDACTION
    return text


def redact_automation_status_for_publish(status: dict[str, Any]) -> dict[str, Any]:
    """Remove local paths before publishing operator automation status."""
    redacted: dict[str, Any] = json.loads(json.dumps(status, default=str))
    redacted.pop("sqlite_path", None)
    if "active_current_dir" in redacted:
        redacted["active_current_dir"] = ACTIVE_CURRENT_REDACTION
    ndr = redacted.get("ndr_pending_review")
    if isinstance(ndr, dict) and ndr.get("queue_dir"):
        ndr["queue_dir"] = ACTIVE_CURRENT_REDACTION
    warnings = redacted.get("warnings")
    if isinstance(warnings, list):
        redacted["warnings"] = [
            _redact_string(w) if isinstance(w, str) else w for w in warnings
        ]
    return redacted


def build_operator_automation_status_snapshot(
    active_current_dir: Path,
    *,
    mirror_cooldown_seconds: int = 60,
) -> dict[str, Any]:
    # active_current_dir is …/reports/out/active/current → reports_dir is …/reports/out
    raw = build_operator_automation_status(
        reports_dir=active_current_dir.parent.parent,
        options=OperatorAutomationStatusOptions(
            skip_cron_inspection=True,
            cron_note="not inspected during snapshot publish",
            mirror_cooldown_seconds=mirror_cooldown_seconds,
        ),
    )
    return redact_automation_status_for_publish(raw)


def upsert_pipeline_kv_snapshot(
    pg_url: str,
    kv_key: str,
    value_json: dict[str, Any],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    """Upsert one ops.pipeline_kv snapshot row."""
    summary = {
        "kv_key": kv_key,
        "published": False,
        "dry_run": dry_run,
        "updated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    if dry_run:
        return summary

    _require_psycopg()
    assert psycopg is not None and Json is not None
    with psycopg.connect(pg_url, autocommit=False) as conn:
        with conn.cursor() as cur:
            if not pg_table_exists(cur, schema="ops", table="pipeline_kv"):
                raise RuntimeError("ops.pipeline_kv is missing; run alembic upgrade head")
            cur.execute(
                """
                INSERT INTO ops.pipeline_kv (kv_key, value_json, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (kv_key) DO UPDATE SET
                  value_json = EXCLUDED.value_json,
                  updated_at = now()
                RETURNING updated_at
                """,
                (kv_key, Json(value_json)),
            )
            row = cur.fetchone()
            updated_at = row[0] if not isinstance(row, dict) else row["updated_at"]
        conn.commit()
    summary["published"] = True
    if updated_at is not None:
        if hasattr(updated_at, "isoformat"):
            summary["updated_at_utc"] = updated_at.isoformat()
        else:
            summary["updated_at_utc"] = str(updated_at)
    return summary


def build_operator_snapshots_bundle(
    *,
    sqlite_path: Path,
    active_current_dir: Path,
    lookback_days: int = 180,
) -> dict[str, Any]:
    gmail_audit = build_gmail_interaction_audit_snapshot(
        sqlite_path,
        lookback_days=lookback_days,
    )
    automation_status = build_operator_automation_status_snapshot(active_current_dir)
    return {
        "gmail_interaction_audit": gmail_audit,
        "operator_automation_status": automation_status,
    }


def publish_operator_dashboard_snapshots(
    pg_url: str,
    *,
    sqlite_path: Path,
    active_current_dir: Path,
    dry_run: bool,
    lookback_days: int = 180,
) -> dict[str, Any]:
    """Build and optionally publish Gmail audit + operator automation snapshots."""
    bundle = build_operator_snapshots_bundle(
        sqlite_path=sqlite_path,
        active_current_dir=active_current_dir,
        lookback_days=lookback_days,
    )
    gmail_audit = bundle["gmail_interaction_audit"]
    automation_status = bundle["operator_automation_status"]

    gmail_publish = upsert_pipeline_kv_snapshot(
        pg_url,
        GMAIL_INTERACTION_AUDIT_KV_KEY,
        gmail_audit,
        dry_run=dry_run,
    )
    automation_publish = upsert_pipeline_kv_snapshot(
        pg_url,
        OPERATOR_AUTOMATION_STATUS_SNAPSHOT_KV_KEY,
        automation_status,
        dry_run=dry_run,
    )

    return {
        "dry_run": dry_run,
        "gmail_interaction_audit": {
            "domain_count": len(gmail_audit.get("domains") or []),
            "lookback_days": gmail_audit.get("lookback_days"),
            "publish": gmail_publish,
        },
        "operator_automation_status": {
            "verdict": automation_status.get("verdict"),
            "publish": automation_publish,
        },
    }
