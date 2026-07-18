"""Read-only operator status report (SQLite, active/current, manifest)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from origenlab_email_pipeline.contacto_gmail_source import sql_predicate_contacto_gmail_source
from origenlab_email_pipeline.operator_cli.daily_core_manifest import (
    MANIFEST_FILENAME,
    summarize_daily_core_run_manifest,
)
from origenlab_email_pipeline.outbound_core import resolve_outbound_gmail_user, resolve_outbound_sent_folders
from origenlab_email_pipeline.outbound_readiness_check import assess_outbound_readiness

_STALE_FASTLAB_SUBSTRINGS = (
    "manual_state_only_pending",
    "outreach_state contacted",
    "contacted without gmail",
)


def _is_stale_fastlab_warning(text: str) -> bool:
    lower = text.lower()
    if "fastlab" not in lower:
        return False
    if "not_contacted" in lower or "corrected" in lower:
        return False
    return any(s in lower for s in _STALE_FASTLAB_SUBSTRINGS) or (
        "contacted" in lower and "not_contacted" not in lower
    )


def _fastlab_email_from_manifest(manifest: dict[str, Any]) -> str:
    notes = (manifest.get("operator_notes") or {}).get("fastlab") or {}
    return str(notes.get("email") or "contacto@fastlab.cl").strip().lower()


def _fetch_outreach_state(conn: sqlite3.Connection, email_norm: str) -> str | None:
    try:
        row = conn.execute(
            "SELECT state FROM outreach_contact_state WHERE contact_email_norm = ?",
            (email_norm,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return str(row[0]).strip().lower() if row and row[0] else None


def build_fastlab_status_warning(
    manifest: dict[str, Any],
    *,
    conn: sqlite3.Connection | None = None,
) -> str | None:
    """Canonical FastLab line for operator_status (read-only)."""
    notes = (manifest.get("operator_notes") or {}).get("fastlab") or {}
    email = _fastlab_email_from_manifest(manifest)
    state = (notes.get("outreach_state") or notes.get("status") or "").strip().lower()
    if conn is not None:
        db_state = _fetch_outreach_state(conn, email)
        if db_state:
            state = db_state
    if state == "not_contacted" or notes.get("status") == "not_contacted_no_sent_evidence":
        return (
            f"FastLab ({email}): corrected to not_contacted; no Gmail Sent evidence; "
            "future outreach requires deliberate manual review."
        )
    if state == "contacted":
        return (
            f"FastLab ({email}): outreach_state contacted without Gmail Sent row — "
            "verify Sent ingest or reset with mark_outreach_state.py."
        )
    return None


def normalize_operator_warnings(
    warnings: list[str],
    manifest: dict[str, Any],
    *,
    conn: sqlite3.Connection | None = None,
) -> list[str]:
    """Drop stale FastLab lines and inject canonical FastLab status from manifest/SQLite."""
    out = [w for w in warnings if not _is_stale_fastlab_warning(w)]
    fastlab = build_fastlab_status_warning(manifest, conn=conn)
    if fastlab:
        out = [w for w in out if "fastlab" not in w.lower()]
        out.append(fastlab)
    return out


def manifest_warnings_for_verdict(
    manifest_warnings: list[str],
    manifest: dict[str, Any],
) -> list[str]:
    """Manifest warnings that should affect READY vs CAUTION (exclude stale FastLab)."""
    notes = (manifest.get("operator_notes") or {}).get("fastlab") or {}
    corrected = notes.get("outreach_state") == "not_contacted"
    out: list[str] = []
    for w in manifest_warnings:
        if corrected and _is_stale_fastlab_warning(w):
            continue
        out.append(w)
    return out


def build_auxiliary_file_status(
    active_current: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Resolve anti-repeat CSV paths per manifest (current/ vs active/)."""
    active_root = active_current.parent
    out: dict[str, Any] = {}
    for rel in manifest.get("auxiliary_files_active_parent") or []:
        out[str(rel)] = _file_freshness(active_root / rel)
    dnr = "do_not_repeat_master.csv"
    if dnr in (manifest.get("canonical_files") or []):
        out[dnr] = _file_freshness(active_current / dnr)
    return out


@dataclass
class OperatorStatusReport:
    """Structured operator status (read-only)."""

    verdict: str  # READY | CAUTION | BLOCKED
    generated_at: str
    sqlite_path: str
    sqlite_exists: bool
    sqlite_size_bytes: int | None
    emails_global_max_date_iso: str | None
    emails_2026_max_date_iso: str | None
    sent: dict[str, Any] = field(default_factory=dict)
    auxiliary_files: dict[str, Any] = field(default_factory=dict)
    canonical_files: dict[str, Any] = field(default_factory=dict)
    equipment_queue: dict[str, Any] = field(default_factory=dict)
    campaign_mode: str | None = None
    current_operator_focus: str | None = None
    manifest: dict[str, Any] = field(default_factory=dict)
    daily_core_run: dict[str, Any] = field(default_factory=dict)
    postgres: dict[str, Any] = field(default_factory=dict)
    api: dict[str, Any] = field(default_factory=dict)
    outbound_readiness: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_json_obj(self) -> dict[str, Any]:
        return asdict(self)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _file_freshness(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "exists": False}
    st = path.stat()
    return {
        "path": str(path),
        "exists": True,
        "size_bytes": st.st_size,
        "mtime_iso": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
    }


def _count_csv_rows(path: Path) -> int | None:
    if not path.is_file():
        return None
    import csv

    with path.open(newline="", encoding="utf-8-sig") as f:
        return sum(1 for _ in csv.DictReader(f))


def _latest_equipment_operator_queue(active_current: Path) -> Path | None:
    candidates = sorted(active_current.glob("equipment_first_operator_queue_*.csv"))
    return candidates[-1] if candidates else None


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"_parse_error": True}


def probe_postgres_status() -> dict[str, Any]:
    """Classify Postgres URL presence and optional read-only ping."""
    import os

    url = (
        (os.environ.get("ORIGENLAB_POSTGRES_URL") or "").strip()
        or (os.environ.get("ALEMBIC_DATABASE_URL") or "").strip()
    )
    out: dict[str, Any] = {
        "status": "parked",
        "url_configured": bool(url),
        "detail": "Postgres is optional; daily outbound uses SQLite only.",
    }
    if not url:
        return out
    lower = url.lower()
    if "scratch" in lower or "127.0.0.1" in lower or "localhost" in lower:
        out["classification"] = "scratch_or_local"
    else:
        out["classification"] = "non_scratch_url_configured"
        out["warnings"] = ["Postgres URL set — do not run migrate --replace without explicit approval."]
    try:
        import psycopg

        with psycopg.connect(url, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        out["reachable"] = True
        out["status"] = "available"
        out["detail"] = "Read-only SELECT 1 succeeded (no writes performed)."
    except Exception as exc:  # noqa: BLE001 — operator diagnostic
        out["reachable"] = False
        out["status"] = "parked"
        out["detail"] = f"URL configured but not reachable: {type(exc).__name__}"
    return out


def compute_verdict(
    *,
    sqlite_exists: bool,
    readiness_verdict: str,
    manifest_warnings: list[str],
    extra_errors: list[str],
) -> str:
    if extra_errors or not sqlite_exists:
        return "BLOCKED"
    if readiness_verdict == "not_ready":
        return "BLOCKED"
    if readiness_verdict in ("ready_with_warnings",) or manifest_warnings:
        return "CAUTION"
    return "READY"


def build_operator_status_report(
    *,
    sqlite_path: Path,
    active_current: Path,
    manifest_path: Path,
    gmail_user: str,
    sent_folders: tuple[str, ...],
    max_staleness_days: float = 14.0,
    immutable: bool = False,
) -> OperatorStatusReport:
    generated_at = _utc_now_iso()
    errors: list[str] = []
    warnings: list[str] = []

    manifest = load_manifest(manifest_path)
    if manifest.get("_parse_error"):
        errors.append(f"manifest.json parse error: {manifest_path}")
        manifest = {}
    manifest_warnings_raw = list(manifest.get("known_warnings") or [])

    daily_core_path = active_current / MANIFEST_FILENAME
    daily_core_run, daily_core_warning = summarize_daily_core_run_manifest(daily_core_path)
    if daily_core_warning:
        warnings.append(daily_core_warning)

    manifest_warnings = manifest_warnings_for_verdict(manifest_warnings_raw, manifest)

    sqlite_exists = sqlite_path.is_file()
    sqlite_conn: sqlite3.Connection | None = None
    size_bytes: int | None = None
    global_max: str | None = None
    max_2026: str | None = None
    sent_info: dict[str, Any] = {}

    if sqlite_exists:
        size_bytes = sqlite_path.stat().st_size
        uri = (
            f"file:{sqlite_path.resolve().as_posix()}?mode=ro&immutable=1"
            if immutable
            else f"file:{sqlite_path.resolve().as_posix()}?mode=ro"
        )
        sqlite_conn = sqlite3.connect(uri, uri=True)
        if immutable:
            sqlite_conn.execute("PRAGMA query_only=ON")
        try:
            row = sqlite_conn.execute("SELECT MAX(date_iso) FROM emails").fetchone()
            global_max = row[0] if row else None
            row2 = sqlite_conn.execute(
                "SELECT MAX(date_iso) FROM emails WHERE date_iso >= '2026-01-01' AND date_iso < '2027-01-01'"
            ).fetchone()
            max_2026 = row2[0] if row2 else None
            if global_max and str(global_max)[:4] not in ("2024", "2025", "2026"):
                warnings.append(
                    f"Global MAX(date_iso) outlier ({global_max}) — prefer 2026-filtered freshness."
                )

            pred = sql_predicate_contacto_gmail_source()
            sent_where = f"""
                {pred}
                  AND (
                    source_file LIKE '%Enviados%'
                    OR source_file LIKE '%Sent%'
                  )
            """
            sent_row = sqlite_conn.execute(
                f"""
                SELECT COUNT(*), MAX(date_iso) FROM emails
                WHERE {sent_where}
                """,
            ).fetchone()
            email_cols = {
                str(r[1])
                for r in sqlite_conn.execute("PRAGMA table_info(emails)").fetchall()
            }
            distinct_mid_count: int | None = None
            missing_mid_count: int | None = None
            if "message_id" in email_cols:
                distinct_mid_row = sqlite_conn.execute(
                    f"""
                    SELECT COUNT(DISTINCT lower(trim(message_id)))
                    FROM emails
                    WHERE {sent_where}
                      AND message_id IS NOT NULL AND trim(message_id) != ''
                    """,
                ).fetchone()
                missing_mid_row = sqlite_conn.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM emails
                    WHERE {sent_where}
                      AND (message_id IS NULL OR trim(message_id) = '')
                    """,
                ).fetchone()
                distinct_mid_count = int(distinct_mid_row[0] or 0) if distinct_mid_row else 0
                missing_mid_count = int(missing_mid_row[0] or 0) if missing_mid_row else 0
            sent_info = {
                "gmail_user": gmail_user,
                "sent_folders_config": list(sent_folders),
                "canonical_sent_row_count": int(sent_row[0] or 0) if sent_row else 0,
                "canonical_sent_max_date_iso": sent_row[1] if sent_row else None,
                "canonical_sent_distinct_message_id_count": distinct_mid_count,
                "canonical_sent_missing_message_id_count": missing_mid_count,
                "comparison_note": (
                    "Stored canonical Sent history may exceed Gmail's current Sent label count "
                    "(duplicates, missing message_id, or retained historical rows). "
                    "Do not assume equality with live Gmail Sent."
                ),
            }

            try:
                readiness = assess_outbound_readiness(
                    sqlite_conn,
                    sqlite_path=sqlite_path,
                    sqlite_exists=True,
                    gmail_user=gmail_user,
                    sent_folders=sent_folders,
                    max_staleness_days=max_staleness_days,
                    strict_commercial_required=False,
                )
                readiness_obj = readiness.to_json_obj()
                warnings.extend(readiness.warnings)
                errors.extend(readiness.errors)
            except sqlite3.OperationalError as exc:
                readiness_obj = {
                    "verdict": "not_ready",
                    "skipped": True,
                    "error": str(exc),
                }
                warnings.append(f"outbound_readiness skipped (schema): {exc}")

            for w in manifest_warnings_raw:
                if w not in warnings:
                    warnings.append(w)
            warnings = normalize_operator_warnings(warnings, manifest, conn=sqlite_conn)
        finally:
            sqlite_conn.close()
            sqlite_conn = None
    else:
        errors.append(f"SQLite not found: {sqlite_path}")
        readiness_obj = assess_outbound_readiness(
            sqlite3.connect(":memory:"),
            sqlite_path=sqlite_path,
            sqlite_exists=False,
            gmail_user=gmail_user,
            sent_folders=sent_folders,
            max_staleness_days=max_staleness_days,
            strict_commercial_required=False,
        ).to_json_obj()
        for w in manifest_warnings_raw:
            if w not in warnings:
                warnings.append(w)
        warnings = normalize_operator_warnings(warnings, manifest, conn=None)

    auxiliary = build_auxiliary_file_status(active_current, manifest)

    canonical_from_manifest = list(manifest.get("canonical_files") or [])
    canonical: dict[str, Any] = {}
    for rel in canonical_from_manifest:
        p = active_current / rel if not str(rel).startswith("/") else Path(rel)
        canonical[rel] = _file_freshness(p)

    equip_path = _latest_equipment_operator_queue(active_current)
    equipment: dict[str, Any] = {"path": str(equip_path) if equip_path else None, "row_count": None}
    if equip_path:
        equipment["row_count"] = _count_csv_rows(equip_path)
        equipment.update(_file_freshness(equip_path))

    verdict = compute_verdict(
        sqlite_exists=sqlite_exists,
        readiness_verdict=str(readiness_obj.get("verdict", "not_ready")),
        manifest_warnings=manifest_warnings,
        extra_errors=errors,
    )

    return OperatorStatusReport(
        verdict=verdict,
        generated_at=generated_at,
        sqlite_path=str(sqlite_path),
        sqlite_exists=sqlite_exists,
        sqlite_size_bytes=size_bytes,
        emails_global_max_date_iso=global_max,
        emails_2026_max_date_iso=max_2026,
        sent=sent_info,
        auxiliary_files=auxiliary,
        canonical_files=canonical,
        equipment_queue=equipment,
        campaign_mode=manifest.get("campaign_mode") if manifest else None,
        current_operator_focus=manifest.get("current_operator_focus") if manifest else None,
        manifest={"path": str(manifest_path), "loaded": bool(manifest), "keys": sorted(manifest.keys())},
        daily_core_run=daily_core_run,
        postgres=probe_postgres_status(),
        api={
            "status": "active",
            "detail": (
                "FastAPI/React is the active read-only operator dashboard; "
                "outbound readiness remains SQLite-based and independent."
            ),
        },
        outbound_readiness=readiness_obj,
        warnings=warnings,
        errors=errors,
    )


def format_human_report(report: OperatorStatusReport) -> str:
    lines = [
        f"Operator status — verdict: {report.verdict}",
        f"Generated: {report.generated_at}",
        "",
        f"SQLite: {report.sqlite_path}",
        f"  exists: {report.sqlite_exists}",
    ]
    if report.sqlite_size_bytes is not None:
        lines.append(f"  size_bytes: {report.sqlite_size_bytes:,}")
    lines.extend(
        [
            f"  emails MAX(date_iso) global: {report.emails_global_max_date_iso}",
            f"  emails MAX(date_iso) 2026: {report.emails_2026_max_date_iso}",
            "",
            "Gmail Sent (stored canonical history):",
        ]
    )
    for k, v in report.sent.items():
        lines.append(f"  {k}: {v}")
    lines.append("")
    if report.campaign_mode is not None:
        lines.append(f"Campaign mode: {report.campaign_mode}")
    if report.current_operator_focus:
        lines.append(f"Operator focus: {report.current_operator_focus}")
    lines.append("")
    lines.append("Equipment-first operator queue:")
    for k, v in report.equipment_queue.items():
        lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append("Auxiliary anti-repeat files:")
    for name, meta in report.auxiliary_files.items():
        lines.append(f"  {name}: exists={meta.get('exists')} mtime={meta.get('mtime_iso', 'n/a')}")
    lines.append("")
    lines.append(f"Postgres: {report.postgres.get('status')} — {report.postgres.get('detail')}")
    lines.append(f"API: {report.api.get('status')} — {report.api.get('detail')}")
    lines.append(f"Outbound readiness: {report.outbound_readiness.get('verdict', 'n/a')}")
    lines.append("")
    lines.append("Daily core last run:")
    dcr = report.daily_core_run
    lines.append(f"  manifest: {dcr.get('path', 'n/a')}")
    lines.append(f"  exists: {dcr.get('exists', False)}")
    if dcr.get("exists") and dcr.get("loaded"):
        lines.append(f"  status: {dcr.get('status', 'n/a')}")
        lines.append(f"  generated_at_utc: {dcr.get('generated_at_utc', 'n/a')}")
        lines.append(f"  returncode: {dcr.get('returncode', 'n/a')}")
        lines.append(f"  steps: {dcr.get('step_count', 'n/a')}")
        lines.append(f"  send_approval: {dcr.get('send_approval', 'n/a')}")
        lines.append(f"  postgres_mirror: {dcr.get('postgres_mirror', 'n/a')}")
    elif dcr.get("parse_error"):
        lines.append("  status: n/a")
        lines.append("  parse_error: true")
    else:
        lines.append("  status: n/a")
        lines.append("  generated_at_utc: n/a")
        lines.append("  returncode: n/a")
        lines.append("  steps: n/a")
        lines.append("  send_approval: n/a")
        lines.append("  postgres_mirror: n/a")
    if report.warnings:
        lines.append("")
        lines.append("Warnings:")
        for w in report.warnings:
            lines.append(f"  - {w}")
    if report.errors:
        lines.append("")
        lines.append("Errors:")
        for e in report.errors:
            lines.append(f"  - {e}")
    return "\n".join(lines)
