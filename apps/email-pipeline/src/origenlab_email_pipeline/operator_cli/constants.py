"""Operator CLI subcommand names, script map, and help strings."""

from __future__ import annotations

GMAIL_INGEST_SCRIPT = "scripts/ingest/05_workspace_gmail_imap_to_sqlite.py"
GMAIL_INGEST_INBOX_FOLDER = "INBOX"
GMAIL_INGEST_SENT_FOLDER = "[Gmail]/Enviados"

MIRROR_DASHBOARD_SYNC_SCRIPT = "scripts/sync/sync_dashboard_postgres_mirror.py"
# Match resolve_postgres_url() in dashboard_postgres_sync / mart_core_postgres_migrate.
POSTGRES_ENV_VARS: tuple[str, ...] = (
    "ORIGENLAB_POSTGRES_URL",
    "ALEMBIC_DATABASE_URL",
    "ORIGENLAB_CLOUD_POSTGRES_URL",
)

# Subcommand -> script path relative to apps/email-pipeline repo root (1:1 wrappers).
SUBCOMMAND_SCRIPTS: dict[str, str] = {
    "status": "scripts/qa/operator_status.py",
    "daily-health": "scripts/qa/run_daily_health_report.py",
    "refresh-safety": "scripts/qa/refresh_outbound_safety_memory.py",
    "validate-csvs": "scripts/qa/validate_campaign_csvs.py",
    "check-readiness": "scripts/qa/check_outbound_readiness.py",
    "post-send-digest": "scripts/qa/build_post_send_digest.py",
    "export-dnr": "scripts/qa/export_do_not_repeat_master.py",
    "ndr-review": "scripts/qa/build_ndr_review_queue.py",
    "contact-universe-review": "scripts/leads/build_contact_universe_review.py",
    "audit-overlap": "scripts/qa/export_contacted_lead_overlap_audit.py",
    "audit-facades": "scripts/qa/audit_module_facades.py",
    "audit-institution-grouping": "scripts/qa/audit_institution_grouping.py",
    "audit-email-mart-feature-scan": "scripts/qa/audit_email_mart_feature_scan.py",
    "build-mart": "scripts/mart/build_business_mart.py",
    "build-email-mart-features": "scripts/mart/build_email_mart_features.py",
    "build-commercial-intel": "scripts/commercial/build_commercial_intel_v1.py",
    "gmail-ingest-help": GMAIL_INGEST_SCRIPT,
}

# Multi-step or special wrappers (not 1:1 SUBCOMMAND_SCRIPTS).
GMAIL_INGEST_COMMANDS: frozenset[str] = frozenset(
    {"gmail-ingest", "gmail-ingest-folders"}
)
MIRROR_DASHBOARD_COMMAND = "mirror-dashboard"
DAILY_CORE_COMMAND = "daily-core"
DAILY_CORE_USAGE = "uv run origenlab daily-core"
AUTO_REFRESH_MAIL_COMMAND = "auto-refresh-mail"
AUTO_REFRESH_MAIL_USAGE = "uv run origenlab auto-refresh-mail"
AUTO_MIRROR_DASHBOARD_COMMAND = "auto-mirror-dashboard"
AUTO_MIRROR_DASHBOARD_USAGE = "uv run origenlab auto-mirror-dashboard"
AUTO_REFRESH_CHILECOMPRA_EQUIPMENT_COMMAND = "auto-refresh-chilecompra-equipment"
AUTO_REFRESH_CHILECOMPRA_EQUIPMENT_USAGE = (
    "uv run origenlab auto-refresh-chilecompra-equipment"
)
OPERATOR_AUTOMATION_STATUS_COMMAND = "operator-automation-status"
OPERATOR_AUTOMATION_STATUS_USAGE = "uv run origenlab operator-automation-status"
NDR_SAFE_AUTO_APPLY_COMMAND = "ndr-safe-auto-apply"
NDR_SAFE_AUTO_APPLY_USAGE = "uv run origenlab ndr-safe-auto-apply"
REFRESH_DASHBOARD_COMMAND = "refresh-dashboard"
REFRESH_DASHBOARD_USAGE = "uv run origenlab refresh-dashboard"
IMPORT_TENDER_ANNEX_BUNDLE_COMMAND = "import-tender-annex-bundle"
IMPORT_TENDER_ANNEX_BUNDLE_USAGE = "uv run origenlab import-tender-annex-bundle"
LOCAL_TENDER_WORKER_COMMAND = "local-tender-worker"
LOCAL_TENDER_WORKER_USAGE = "uv run origenlab local-tender-worker"
SPECIAL_COMMANDS: frozenset[str] = GMAIL_INGEST_COMMANDS | frozenset(
    {
        MIRROR_DASHBOARD_COMMAND,
        REFRESH_DASHBOARD_COMMAND,
        DAILY_CORE_COMMAND,
        AUTO_REFRESH_MAIL_COMMAND,
        AUTO_MIRROR_DASHBOARD_COMMAND,
        AUTO_REFRESH_CHILECOMPRA_EQUIPMENT_COMMAND,
        OPERATOR_AUTOMATION_STATUS_COMMAND,
        NDR_SAFE_AUTO_APPLY_COMMAND,
        IMPORT_TENDER_ANNEX_BUNDLE_COMMAND,
        LOCAL_TENDER_WORKER_COMMAND,
    }
)

CLI_COMMAND_NAMES: tuple[str, ...] = tuple(SUBCOMMAND_SCRIPTS.keys()) + tuple(
    sorted(SPECIAL_COMMANDS)
)

# Subcommands that only run the target script ``--help`` (no passthrough).
HELP_ONLY_SUBCOMMANDS: frozenset[str] = frozenset({"gmail-ingest-help"})

SUBCOMMAND_HELP: dict[str, str] = {
    "status": "Operator READY / CAUTION / BLOCKED snapshot (operator_status.py)",
    "daily-health": "Combined daily health report (run_daily_health_report.py)",
    "refresh-safety": "Outbound safety memory refresh chain (refresh_outbound_safety_memory.py)",
    "validate-csvs": "Campaign CSV contract validation (validate_campaign_csvs.py)",
    "check-readiness": "Outbound readiness checks (check_outbound_readiness.py)",
    "post-send-digest": "Post-send digest artifacts (build_post_send_digest.py)",
    "export-dnr": "Export do-not-repeat master lists (export_do_not_repeat_master.py)",
    "ndr-review": "NDR human-review batches — read-only (build_ndr_review_queue.py)",
    "contact-universe-review": (
        "Read-only contact universe review export for outreach planning "
        "(build_contact_universe_review.py)"
    ),
    "audit-overlap": "Contacted-lead overlap audit CSV (export_contacted_lead_overlap_audit.py)",
    "audit-facades": "Read-only module facade / duplicate basename audit (audit_module_facades.py)",
    "audit-institution-grouping": (
        "Read-only institution/domain grouping audit from business mart (audit_institution_grouping.py) — "
        "reports only; not send safety"
    ),
    "audit-email-mart-feature-scan": (
        "Read-only parity audit: email body scan vs email_mart_features scan "
        "(audit_email_mart_feature_scan.py) — no mart table writes"
    ),
    "build-mart": (
        "Business mart rebuild (build_business_mart.py) — break-glass: optional --rebuild deletes mart tables"
    ),
    "build-email-mart-features": (
        "Precompute email_mart_features from emails (build_email_mart_features.py) — dry-run default; "
        "--apply writes missing/stale rows; --missing-only inserts new rows only"
    ),
    "build-commercial-intel": (
        "Commercial intel incremental builder (build_commercial_intel_v1.py) — writes SQLite commercial_* "
        "tables; --rebuild is break-glass"
    ),
    "gmail-ingest": (
        "Safe daily Gmail ingest: INBOX then Sent ([Gmail]/Enviados), --skip-duplicate-message-id; "
        "rejects --replace-source"
    ),
    "gmail-ingest-folders": (
        "List IMAP folder labels on the ingest script (use if [Gmail]/Enviados differs)"
    ),
    "gmail-ingest-help": (
        "Show Gmail Workspace ingest --help only. For daily ingest use gmail-ingest."
    ),
    "mirror-dashboard": (
        "Postgres dashboard mirror sync (dry-run default); --apply writes; "
        "--alembic --apply runs alembic upgrade head first"
    ),
    "refresh-dashboard": (
        "Orchestrated Gmail→mart→commercial→safety→digest→status→mirror workflow (plan-only default)"
    ),
    "daily-core": (
        "Daily operating alias: --apply runs feature refresh + feature-backed mart rebuild "
        "(never includes mirror); plan-only by default"
    ),
    "auto-refresh-mail": (
        "Debounced mailbox auto-refresh: --once probes INBOX/Sent UID counts and may run daily-core "
        "--apply after quiet/cooldown gates (dry-run default)"
    ),
    "auto-mirror-dashboard": (
        "Debounced dashboard mirror: --once publishes to Postgres after successful daily-core and clean "
        "mail state (dry-run default; --apply requires --allow-non-scratch-postgres)"
    ),
    "auto-refresh-chilecompra-equipment": (
        "ChileCompra equipment queue refresh: --once --apply fetches API queue, writes audit, and "
        "publishes canonical dashboard CSV (dry-run default; does not call auto-mirror-dashboard)"
    ),
    "operator-automation-status": (
        "Read-only automation health: daily-core manifest, mail auto-refresh, dashboard auto-mirror, "
        "user crontab (local state only; optional --json, --skip-cron-inspection)"
    ),
    "ndr-safe-auto-apply": (
        "Dry-run or guarded Batch A NDR apply from ndr_review_queue artifacts "
        "(--batch A --dry-run | --apply --operator NAME --confirm-reviewed)"
    ),
    "import-tender-annex-bundle": (
        "Import an operator-supplied complete attachment ZIP already on local disk "
        "(--tender-code CODE --zip PATH [--declare-complete]); no network I/O, no "
        "browser automation; reports only, does not publish"
    ),
    "local-tender-worker": (
        "Watch local OriginLab tender tickets + Licitaciones ZIP downloads; "
        "run extraction/OCR/T1 on this workstation and publish only structured JSON"
    ),
}
