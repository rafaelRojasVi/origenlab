"""Read-only daily pipeline health report (no Gmail/DB mutations)."""

from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal

from origenlab_email_pipeline.business_mart import domain_of
from origenlab_email_pipeline.lead_research.lead_research_operational_overlay import (
    CLASS_RESEARCH_ONLY,
    CLASS_SAME_DOMAIN_CONTACTED_REVIEW,
    apply_operational_overlay_to_prospect,
    load_operational_indexes_from_sqlite,
    normalize_prospect_email,
)
from origenlab_email_pipeline.lead_research.prospectos_safety_drift import (
    CONTACTED_STATES,
    load_raw_active_prospects,
    run_prospectos_safety_drift_audit,
)
from origenlab_email_pipeline.mart_core_postgres_migrate import (
    connect_sqlite_readonly,
    resolve_postgres_url,
)
from origenlab_email_pipeline.ndr_contacto_scan import (
    scan_ndr_planned_recipients,
    summarize_ndr_backlog,
)
from origenlab_email_pipeline.operator_status_report import build_operator_status_report
from origenlab_email_pipeline.outbound_core import (
    resolve_outbound_gmail_user,
    resolve_outbound_sent_folders,
)
from origenlab_email_pipeline.outbound_sidecar_mirror_verify import (
    compare_outbound_sidecar_mirror,
    postgres_outbound_sidecar_counts,
    sqlite_outbound_sidecar_counts,
)

HealthVerdict = Literal["READY", "REVIEW_NEEDED", "BLOCKED"]
SCHEMA_VERSION = "1"
NDR_SCAN_LIMIT = 50_000

VERIFIER_JSON_CANDIDATES: tuple[tuple[str, Path], ...] = (
    ("outbound_sidecar_mirror", Path("/tmp/outbound_sidecar_mirror_verify.json")),
    ("lead_research_mirror", Path("/tmp/lead_research_mirror_verify.json")),
    ("render_dashboard_mirror", Path("/tmp/render_dashboard_mirror_verify.json")),
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_date_label(when: date | None = None) -> str:
    d = when or date.today()
    return d.strftime("%Y_%m_%d")


def default_out_dir(repo_root: Path, date_label: str) -> Path:
    return (
        repo_root
        / "reports"
        / "out"
        / "active"
        / "current"
        / f"daily_health_report_{date_label}"
    )


def classify_daily_health(
    *,
    collection_errors: list[str],
    operator_verdict: str | None,
    mirror_file_ok: bool | None,
    postgres_live_ok: bool | None,
    net_new_ndr: int,
    falta_email_stale_display: int,
    operator_caution_is_review: bool = True,
) -> tuple[HealthVerdict, list[str]]:
    """Pure health verdict for tests and reporting."""
    reasons: list[str] = []
    if collection_errors:
        return "BLOCKED", list(collection_errors)
    if operator_verdict == "BLOCKED":
        return "BLOCKED", ["operator_status=BLOCKED"]
    if mirror_file_ok is False:
        return "BLOCKED", ["mirror verifier JSON reports failure"]
    if postgres_live_ok is False:
        return "BLOCKED", ["sqlite/postgres outbound sidecar parity failure"]
    if net_new_ndr > 0:
        reasons.append(f"net_new_ndr_backlog={net_new_ndr}")
    if falta_email_stale_display > 0:
        reasons.append(f"falta_email_stale_display_queue={falta_email_stale_display}")
    if operator_caution_is_review and operator_verdict == "CAUTION":
        reasons.append("operator_status=CAUTION")
    if reasons:
        return "REVIEW_NEEDED", reasons
    return "READY", []


def _verifier_json_ok(body: dict[str, Any]) -> bool:
    if "ok" in body:
        return bool(body["ok"])
    dash = body.get("render_dashboard_assertions")
    if isinstance(dash, dict) and "passed" in dash:
        return bool(dash["passed"])
    failures = body.get("failures")
    if isinstance(failures, list):
        return len(failures) == 0
    return False


def load_verifier_json_status() -> dict[str, Any]:
    """Read latest mirror verifier JSON files when present (no refresh)."""
    verifiers: dict[str, Any] = {}
    any_present = False
    all_ok = True
    for name, path in VERIFIER_JSON_CANDIDATES:
        entry: dict[str, Any] = {"path": str(path), "present": path.is_file()}
        if not path.is_file():
            verifiers[name] = entry
            continue
        any_present = True
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            entry["parse_error"] = str(exc)
            entry["ok"] = False
            all_ok = False
        else:
            entry["ok"] = _verifier_json_ok(body)
            entry["errors"] = list(body.get("errors") or [])
            dash_assert = body.get("render_dashboard_assertions")
            if isinstance(dash_assert, dict):
                entry["render_dashboard_assertions"] = dash_assert
                entry["errors"] = list(entry["errors"]) + list(dash_assert.get("failures") or [])
            if not entry["ok"]:
                all_ok = False
            entry["generated_at"] = body.get("generated_at")
        verifiers[name] = entry
    aggregate_ok: bool | None
    if not any_present:
        aggregate_ok = None
    else:
        aggregate_ok = all_ok
    return {"verifiers": verifiers, "aggregate_ok": aggregate_ok}


def _domains_with_contacted_outreach(conn: sqlite3.Connection) -> set[str]:
    domains: set[str] = set()
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='outreach_contact_state'"
    ).fetchone():
        return domains
    for row in conn.execute(
        "SELECT contact_email_norm, state FROM outreach_contact_state"
    ):
        state = str(row[1] or "").lower()
        if state not in CONTACTED_STATES:
            continue
        email = normalize_prospect_email(row[0])
        dom = domain_of(email) if email else ""
        if dom:
            domains.add(dom)
    return domains


def collect_falta_email_stale_display_rows(
    conn: sqlite3.Connection,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Rows still showing Falta email after overlay despite same-domain evidence (should be rare)."""
    raw = load_raw_active_prospects(conn)
    indexes = load_operational_indexes_from_sqlite(conn)
    contacted_domains = indexes.domains_with_contacted
    stale: list[dict[str, Any]] = []
    raw_falta = 0
    overlay_same_domain = 0
    for prospect in raw:
        email = normalize_prospect_email(prospect.get("email"))
        if email:
            continue
        classification = str(prospect.get("classification") or "")
        if classification != CLASS_RESEARCH_ONLY:
            continue
        raw_falta += 1
        domain = (str(prospect.get("domain") or "").strip().lower()) or ""
        gmail_sent = int(prospect.get("gmail_sent_count") or 0)
        gmail_last = prospect.get("gmail_last_contacted_at")
        evidence: list[str] = []
        if domain and domain in contacted_domains:
            evidence.append("domain_outreach_contacted")
        if gmail_sent > 0:
            evidence.append("gmail_sent_count")
        if gmail_last:
            evidence.append("gmail_last_contacted_at")
        overlay = apply_operational_overlay_to_prospect(dict(prospect), indexes)
        if overlay.get("classification") == CLASS_SAME_DOMAIN_CONTACTED_REVIEW:
            overlay_same_domain += 1
            continue
        if not evidence:
            continue
        if overlay.get("classification") != CLASS_RESEARCH_ONLY:
            continue
        stale.append(
            {
                "prospect_key": prospect.get("prospect_key"),
                "organization_name": prospect.get("organization_name"),
                "domain": domain,
                "raw_classification": classification,
                "overlay_classification": overlay.get("classification"),
                "raw_status": prospect.get("status"),
                "evidence": ";".join(evidence),
                "gmail_sent_count": gmail_sent,
                "gmail_last_contacted_at": gmail_last,
                "recommended_action": "overlay_should_have_upgraded",
            }
        )
    stale.sort(key=lambda r: (str(r.get("domain") or ""), str(r.get("prospect_key") or "")))
    counts = {
        "raw_falta_email_count": raw_falta,
        "falta_email_overlay_same_domain_count": overlay_same_domain,
        "falta_email_stale_display_count": len(stale),
        "falta_email_truly_missing_count": raw_falta - overlay_same_domain - len(stale),
    }
    return stale, counts


def inspect_latest_post_send_digest(active_current: Path) -> dict[str, Any]:
    """Read newest post_send_safety_summary_*.json without building a digest."""
    pattern = "post_send_safety_summary_*.json"
    candidates = sorted(active_current.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return {"found": False, "pattern": pattern, "active_current": str(active_current)}
    latest = candidates[0]
    out: dict[str, Any] = {
        "found": True,
        "path": str(latest),
        "mtime_iso": datetime.fromtimestamp(
            latest.stat().st_mtime, tz=timezone.utc
        ).replace(microsecond=0).isoformat(),
    }
    try:
        body = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        out["parse_error"] = str(exc)
        return out
    out["generated_at"] = body.get("generated_at")
    out["since_days"] = body.get("since_days")
    out["bounce_suppression_count"] = body.get("bounce_suppression_count")
    out["ndr_rows"] = body.get("ndr_rows")
    return out


def verify_postgres_outbound_live(
    conn: sqlite3.Connection,
    *,
    postgres_url: str | None,
) -> dict[str, Any]:
    """Optional live SQLite vs Postgres outbound.* parity (read-only)."""
    if postgres_url is None:
        return {"skipped": True, "reason": "no postgres url"}
    try:
        import psycopg
    except ImportError:
        return {"skipped": True, "reason": "psycopg not installed"}

    sqlite_counts = sqlite_outbound_sidecar_counts(conn)
    try:
        with psycopg.connect(postgres_url) as pconn:
            with pconn.cursor() as cur:
                postgres_counts = postgres_outbound_sidecar_counts(cur)
    except Exception as exc:  # noqa: BLE001 — surface as health error
        return {"skipped": False, "ok": False, "errors": [str(exc)]}

    report = compare_outbound_sidecar_mirror(sqlite_counts, postgres_counts)
    return {
        "skipped": False,
        "ok": bool(report.get("ok")),
        "errors": list(report.get("errors") or []),
        "sqlite_counts": sqlite_counts,
        "postgres_counts": postgres_counts,
    }


@dataclass
class DailyHealthReportResult:
    schema_version: str
    generated_at: str
    date_label: str
    since_days: int
    health_verdict: HealthVerdict
    health_reasons: list[str]
    sqlite_path: str
    ndr: dict[str, Any] = field(default_factory=dict)
    suppression_outreach: dict[str, Any] = field(default_factory=dict)
    operator_status: dict[str, Any] = field(default_factory=dict)
    mirror: dict[str, Any] = field(default_factory=dict)
    prospectos: dict[str, Any] = field(default_factory=dict)
    post_send_digest: dict[str, Any] = field(default_factory=dict)
    collection_errors: list[str] = field(default_factory=list)
    out_dir: Path | None = None

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "date_label": self.date_label,
            "since_days": self.since_days,
            "health_verdict": self.health_verdict,
            "health_reasons": self.health_reasons,
            "sqlite_path": self.sqlite_path,
            "ndr": self.ndr,
            "suppression_outreach": self.suppression_outreach,
            "operator_status": self.operator_status,
            "mirror": self.mirror,
            "prospectos": self.prospectos,
            "post_send_digest": self.post_send_digest,
            "collection_errors": self.collection_errors,
        }


def build_daily_health_report(
    *,
    repo_root: Path,
    sqlite_path: Path,
    active_current: Path,
    manifest_path: Path,
    out_dir: Path,
    since_days: int = 2,
    date_label: str | None = None,
    skip_postgres: bool = False,
    skip_ndr: bool = False,
    gmail_user: str | None = None,
    sent_folders: tuple[str, ...] | None = None,
    postgres_url: str | None = None,
) -> DailyHealthReportResult:
    """Collect read-only health signals and write report artifacts under ``out_dir``."""
    generated_at = _utc_now_iso()
    label = date_label or default_date_label()
    errors: list[str] = []
    ndr_section: dict[str, Any] = {"skipped": skip_ndr}
    suppression_outreach: dict[str, Any] = {}
    operator_section: dict[str, Any] = {}
    prospectos_section: dict[str, Any] = {}
    post_send_section: dict[str, Any] = {}
    ndr_csv_rows: list[dict[str, Any]] = []
    prospectos_csv_rows: list[dict[str, Any]] = []

    if not sqlite_path.is_file():
        errors.append(f"sqlite not found: {sqlite_path}")

    mirror_files = load_verifier_json_status()
    mirror_section: dict[str, Any] = {"file_verifiers": mirror_files}
    postgres_live: dict[str, Any] = {"skipped": True}

    net_new_ndr = 0
    falta_stale = 0

    if not errors:
        conn = connect_sqlite_readonly(sqlite_path)
        try:
            suppression_outreach = sqlite_outbound_sidecar_counts(conn)

            if not skip_ndr:
                try:
                    planned, scanned, skipped_no_rcpt = scan_ndr_planned_recipients(
                        conn, since_days=since_days, limit=NDR_SCAN_LIMIT
                    )
                    backlog = summarize_ndr_backlog(conn, planned)
                    net_new_ndr = int(backlog["net_new_count"])
                    ndr_csv_rows = list(backlog["net_new_rows"])
                    ndr_section = {
                        "skipped": False,
                        "since_days": since_days,
                        "scanned_rows": scanned,
                        "skipped_no_recipient": skipped_no_rcpt,
                        **{k: v for k, v in backlog.items() if k != "net_new_rows"},
                    }
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"ndr_scan_failed: {exc}")
                    ndr_section = {"skipped": False, "error": str(exc)}

            try:
                drift_dir = out_dir / "_prospectos_drift_detail"
                drift = run_prospectos_safety_drift_audit(
                    conn,
                    sqlite_path=sqlite_path,
                    out_dir=drift_dir,
                    generated_at=generated_at,
                )
                stale_rows, falta_counts = collect_falta_email_stale_display_rows(conn)
                falta_stale = falta_counts["falta_email_stale_display_count"]
                prospectos_section = {
                    **drift.summary,
                    **falta_counts,
                    "drift_detail_dir": str(drift_dir),
                }
                prospectos_csv_rows = stale_rows
            except Exception as exc:  # noqa: BLE001
                errors.append(f"prospectos_audit_failed: {exc}")

            if not skip_postgres:
                try:
                    pg_url = postgres_url or resolve_postgres_url(None)
                    postgres_live = verify_postgres_outbound_live(conn, postgres_url=pg_url)
                except Exception as exc:  # noqa: BLE001
                    postgres_live = {"skipped": False, "ok": False, "errors": [str(exc)]}
            mirror_section["live_postgres"] = postgres_live
        finally:
            conn.close()

    try:
        op_report = build_operator_status_report(
            sqlite_path=sqlite_path,
            active_current=active_current,
            manifest_path=manifest_path,
            gmail_user=gmail_user or resolve_outbound_gmail_user(),
            sent_folders=sent_folders or resolve_outbound_sent_folders(),
        )
        operator_section = {
            "verdict": op_report.verdict,
            "warnings": list(op_report.warnings),
            "errors": list(op_report.errors),
            "postgres": op_report.postgres,
            "outbound_readiness": op_report.outbound_readiness,
        }
    except Exception as exc:  # noqa: BLE001
        errors.append(f"operator_status_failed: {exc}")
        operator_section = {"error": str(exc)}

    try:
        post_send_section = inspect_latest_post_send_digest(active_current)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"post_send_digest_inspect_failed: {exc}")

    postgres_ok: bool | None
    if postgres_live.get("skipped"):
        postgres_ok = None
    else:
        postgres_ok = bool(postgres_live.get("ok"))

    verdict, reasons = classify_daily_health(
        collection_errors=errors,
        operator_verdict=operator_section.get("verdict"),
        mirror_file_ok=mirror_files.get("aggregate_ok"),
        postgres_live_ok=postgres_ok,
        net_new_ndr=net_new_ndr,
        falta_email_stale_display=falta_stale,
    )

    result = DailyHealthReportResult(
        schema_version=SCHEMA_VERSION,
        generated_at=generated_at,
        date_label=label,
        since_days=since_days,
        health_verdict=verdict,
        health_reasons=reasons,
        sqlite_path=str(sqlite_path),
        ndr=ndr_section,
        suppression_outreach=suppression_outreach,
        operator_status=operator_section,
        mirror=mirror_section,
        prospectos=prospectos_section,
        post_send_digest=post_send_section,
        collection_errors=errors,
        out_dir=out_dir,
    )

    _write_report_artifacts(
        result,
        out_dir=out_dir,
        ndr_csv_rows=ndr_csv_rows,
        prospectos_csv_rows=prospectos_csv_rows,
    )
    return result


def _write_report_artifacts(
    result: DailyHealthReportResult,
    *,
    out_dir: Path,
    ndr_csv_rows: list[dict[str, Any]],
    prospectos_csv_rows: list[dict[str, Any]],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = result.to_summary_dict()
    (out_dir / "daily_health_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    verifier_payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": result.generated_at,
        **result.mirror.get("file_verifiers", {}),
        "live_postgres": result.mirror.get("live_postgres"),
    }
    (out_dir / "verifier_status.json").write_text(
        json.dumps(verifier_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    ndr_fields = ["email", "proposed_code", "date_iso", "email_id", "subject_snippet"]
    _write_csv(out_dir / "ndr_backlog_summary.csv", ndr_fields, ndr_csv_rows)

    prospectos_fields = [
        "prospect_key",
        "organization_name",
        "domain",
        "raw_classification",
        "raw_status",
        "evidence",
        "gmail_sent_count",
        "gmail_last_contacted_at",
        "recommended_action",
    ]
    _write_csv(out_dir / "prospectos_staleness_summary.csv", prospectos_fields, prospectos_csv_rows)

    (out_dir / "DAILY_HEALTH_SUMMARY.md").write_text(
        _format_daily_health_md(result),
        encoding="utf-8",
    )
    (out_dir / "NEXT_HUMAN_ACTIONS.md").write_text(
        _format_next_human_actions(result),
        encoding="utf-8",
    )


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _format_daily_health_md(result: DailyHealthReportResult) -> str:
    ndr = result.ndr
    prospectos = result.prospectos
    sup = result.suppression_outreach
    lines = [
        f"# Daily pipeline health — {result.date_label}",
        "",
        f"- **Generated (UTC):** {result.generated_at}",
        f"- **Health verdict:** `{result.health_verdict}`",
        f"- **Reasons:** {', '.join(result.health_reasons) or '—'}",
        f"- **SQLite:** `{result.sqlite_path}`",
        f"- **NDR window:** {result.since_days} day(s)",
        "",
        "## NDR backlog (dry-run)",
        "",
        "| Metric | Value |",
        "|--------|------:|",
        f"| Scanned contacto rows | {ndr.get('scanned_rows', '—')} |",
        f"| Planned distinct | {ndr.get('planned_distinct', '—')} |",
        f"| Already suppressed | {ndr.get('already_suppressed', '—')} |",
        f"| **Net-new** | **{ndr.get('net_new_count', '—')}** |",
        "",
        "## Suppression / outreach (SQLite)",
        "",
        "| Key | Count |",
        "|-----|------:|",
        f"| email_suppression_total | {sup.get('email_suppression_total', '—')} |",
        f"| bounce_suppressions | {sup.get('bounce_suppressions', '—')} |",
        f"| outreach_contacted | {sup.get('outreach_contacted', '—')} |",
        "",
        "## Prospectos drift",
        "",
        "| Metric | Value |",
        "|--------|------:|",
        f"| Raw Falta-email rows | {prospectos.get('raw_falta_email_count', '—')} |",
        f"| Falta-email stale display queue | {prospectos.get('falta_email_stale_display_count', '—')} |",
        f"| Overlay mismatch count | {prospectos.get('mismatches_count', '—')} |",
        f"| Suppressed not raw blocked | {prospectos.get('suppressed_not_raw_blocked_count', '—')} |",
        "",
        "## Operator status",
        "",
        f"- Verdict: `{result.operator_status.get('verdict', '—')}`",
        "",
        "## Mirror verifiers",
        "",
    ]
    file_ver = result.mirror.get("file_verifiers", {})
    for name, entry in (file_ver.get("verifiers") or {}).items():
        present = entry.get("present")
        ok = entry.get("ok")
        lines.append(f"- **{name}:** present={present} ok={ok}")
    live = result.mirror.get("live_postgres") or {}
    if live.get("skipped"):
        lines.append(f"- Live Postgres parity: skipped ({live.get('reason', '')})")
    else:
        lines.append(f"- Live Postgres parity: ok={live.get('ok')}")
    if result.collection_errors:
        lines.extend(["", "## Collection errors", ""])
        for err in result.collection_errors:
            lines.append(f"- {err}")
    lines.append("")
    return "\n".join(lines)


def _format_next_human_actions(result: DailyHealthReportResult) -> str:
    lines = [
        "# Next human actions",
        "",
        f"Health verdict: **{result.health_verdict}**",
        "",
    ]
    if result.health_verdict == "BLOCKED":
        lines.extend(
            [
                "1. Resolve blockers before outreach or suppression apply.",
                "2. Re-run mirror verifiers and fix SQLite/Postgres parity if reported.",
                "3. Fix operator_status errors; confirm SQLite path and manifest.",
            ]
        )
    elif result.health_verdict == "REVIEW_NEEDED":
        net_new = int(result.ndr.get("net_new_count") or 0)
        falta = int(result.prospectos.get("falta_email_stale_display_count") or 0)
        if net_new:
            lines.append(
                f"1. Review NDR net-new backlog ({net_new} addresses): "
                "`flag_ndr_bounces_from_contacto.py` dry-run, then targeted `--emails-file` apply."
            )
        if falta:
            lines.append(
                f"2. Review Falta-email display queue ({falta} rows): "
                "see `prospectos_staleness_summary.csv` and drift detail under `_prospectos_drift_detail/`."
            )
        lines.append(
            "3. Mirror/operator checks passed or were skipped — do not treat LISTO/mirror_ok as send approval."
        )
    else:
        lines.extend(
            [
                "1. No mandatory review queues detected in this window.",
                "2. Continue normal read-only monitoring; re-run after ingest or sends.",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def exit_code_for_result(
    result: DailyHealthReportResult,
    *,
    fail_on_blocked: bool,
) -> int:
    if result.health_verdict == "BLOCKED":
        return 2 if fail_on_blocked else 1
    if result.health_verdict == "REVIEW_NEEDED":
        return 1
    return 0
