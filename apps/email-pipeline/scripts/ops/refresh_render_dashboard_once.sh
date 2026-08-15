#!/usr/bin/env bash
# Manual one-shot refresh: local SQLite → Render Postgres dashboard mirror.
#
# SAFETY (default):
#   - No Gmail sends; ingest is read-only IMAP (optional, off by default).
#   - No Gmail mutation (no --replace-source).
#   - No outreach-state writes; no refresh_outbound_safety_memory.
#   - No build_business_mart.py --rebuild (incremental mart only).
#   - No build_commercial_intel_v1.py --rebuild (incremental commercial only).
#   - No SQLite backup; no destructive SQL; no dashboard/API writes.
#   - No commercial ledger writes (deal mirror reads SQLite only).
#
# Required env:
#   ORIGENLAB_SQLITE_PATH          local canonical SQLite (read in place)
#   ORIGENLAB_CLOUD_POSTGRES_URL   Render external Postgres URL
#
# Optional env:
#   RUN_GMAIL_INGEST=1             run Gmail ingest before sync (default 0)
#   RUN_COMMERCIAL_DEAL_MIRROR=1   sync+verify commercial.deal after dashboard mirror (default 0)
#   RUN_CATALOG_MIRROR=1           build SQLite catalog + sync+verify catalog.* (default 0)
#   RUN_LEAD_RESEARCH_MIRROR=1     build SQLite lead_research + sync lead_intel.* (default 0)
#   RUN_OUTBOUND_SIDECAR_MIRROR=1  sync outbound sidecars after dashboard mirror (default 1)
#   RUN_W1_CLOUD_SYNC=1            sync W1 institution-prospect bundle to Render persistent disk
#                                   after dashboard mirror (default 0). Requires SSH config:
#                                   ORIGENLAB_RENDER_SSH_HOST / _SSH_USER / _SSH_KEY.
#                                   Failure is non-fatal: local W1 publication remains authoritative.
#   DASHBOARD_FAST=1               fast daily mode (canonical/recent rows + dashboard-only mirror sync)
#   GMAIL_SINCE_DAYS=14            bound IMAP fetch when ingest runs
#   ORIGENLAB_GMAIL_SENT_FOLDER    default "[Gmail]/Enviados"
#   ORIGENLAB_EXPECT_EQUIPMENT_COUNT  default 9 (verify assertion)
#   ORIGENLAB_SYNC_UPDATED_BY / ORIGENLAB_SYNC_REASON  passed to mirror sync
#
# See docs/REFRESH_RENDER_DASHBOARD_ONCE.md
set -eo pipefail

PIPE="$(cd "$(dirname "$0")/../.." && pwd)"

# shellcheck source=scripts/ops/_load_pipeline_dotenv.sh
source "${PIPE}/scripts/ops/_load_pipeline_dotenv.sh"
load_pipeline_dotenv "$PIPE"

SQLITE_PATH="${ORIGENLAB_SQLITE_PATH:-$HOME/data/origenlab-email/sqlite/emails.sqlite}"
CLOUD_PG_URL="${ORIGENLAB_CLOUD_POSTGRES_URL:-}"
RUN_GMAIL_INGEST="${RUN_GMAIL_INGEST:-0}"
RUN_COMMERCIAL_DEAL_MIRROR="${RUN_COMMERCIAL_DEAL_MIRROR:-0}"
RUN_CATALOG_MIRROR="${RUN_CATALOG_MIRROR:-0}"
RUN_LEAD_RESEARCH_MIRROR="${RUN_LEAD_RESEARCH_MIRROR:-0}"
RUN_OUTBOUND_SIDECAR_MIRROR="${RUN_OUTBOUND_SIDECAR_MIRROR:-1}"
# W1 cloud sync is opt-in (default 0) because:
#   - It requires SSH credentials (ORIGENLAB_RENDER_SSH_HOST/USER/KEY).
#   - Failure is non-fatal: the local W1 publication already succeeded before
#     this script runs, and the Render API degrades gracefully (reduced_mode=true)
#     rather than serving corrupt data.
#   - It must NOT be part of the core ChileCompra auto-refresh transaction.
RUN_W1_CLOUD_SYNC="${RUN_W1_CLOUD_SYNC:-0}"
DASHBOARD_FAST="${DASHBOARD_FAST:-0}"
GMAIL_SINCE_DAYS="${GMAIL_SINCE_DAYS:-14}"
GMAIL_SENT_FOLDER="${ORIGENLAB_GMAIL_SENT_FOLDER:-[Gmail]/Enviados}"
EXPECT_EQUIPMENT="${ORIGENLAB_EXPECT_EQUIPMENT_COUNT:-9}"
DASHBOARD_VERIFY_JSON="/tmp/render_dashboard_mirror_verify.json"
COMMERCIAL_VERIFY_JSON="/tmp/commercial_deals_mirror_verify.json"
CATALOG_VERIFY_JSON="/tmp/catalog_postgres_mirror_verify.json"
LEAD_RESEARCH_VERIFY_JSON="/tmp/lead_research_mirror_verify.json"
OUTBOUND_SIDECAR_VERIFY_JSON="/tmp/outbound_sidecar_mirror_verify.json"

_canonical_gmail_count_sql() {
  sqlite3 "$SQLITE_PATH" \
    "SELECT COUNT(*), MAX(date_iso) FROM emails WHERE source_file LIKE 'gmail:contacto@origenlab.cl/%';"
}

_fast_mode_mart_health_check() {
  sqlite3 "$SQLITE_PATH" <<'SQL'
SELECT
  (SELECT COUNT(*) FROM contact_master),
  (SELECT COUNT(*) FROM organization_master),
  (SELECT COUNT(*) FROM opportunity_signals),
  (SELECT COUNT(*) FROM emails WHERE source_file LIKE 'gmail:contacto@origenlab.cl/%');
SQL
}

echo "== OrigenLab: refresh Render dashboard once =="
echo "Safety: no sends, no Gmail mutation, no mart/commercial --rebuild, no outreach writes, no deploy."
if [[ "$DASHBOARD_FAST" == "1" ]]; then
  echo "Mode: DASHBOARD_FAST=1 (safe fast mode; reuses full mart baseline, no partial mart writes)"
  echo "Recovery note: if fast mode was run with partial mart writes in older versions, restore SQLite mart via:"
  echo "  uv run python scripts/mart/build_business_mart.py --rebuild"
else
  echo "Mode: default (full mart scan + standard mirror sync)"
fi

if [[ ! -f "$SQLITE_PATH" ]]; then
  echo "ERROR: SQLite not found: $SQLITE_PATH" >&2
  echo "Set ORIGENLAB_SQLITE_PATH to the local canonical DB." >&2
  exit 2
fi

if [[ -z "$CLOUD_PG_URL" ]]; then
  echo "ERROR: ORIGENLAB_CLOUD_POSTGRES_URL is not set (Render external Postgres URL)." >&2
  exit 2
fi

export ORIGENLAB_SQLITE_PATH="$SQLITE_PATH"
export ORIGENLAB_CLOUD_POSTGRES_URL="$CLOUD_PG_URL"

cd "$PIPE"

# Fail early on placeholder/invalid URL; export psycopg URL for sync + verify in this shell.
# shellcheck source=scripts/ops/_cloud_postgres_env.sh
source "${PIPE}/scripts/ops/_cloud_postgres_env.sh"
if ! cloud_postgres_prepare_env "$PIPE"; then
  exit 2
fi

echo ""
echo "-- Preflight: SQLite readable (canonical Gmail count before) --"
SQLITE_CANONICAL_BEFORE="$(_canonical_gmail_count_sql)"
echo "$SQLITE_CANONICAL_BEFORE"

if [[ "$RUN_GMAIL_INGEST" == "1" ]]; then
  echo ""
  echo "-- Optional Gmail ingest (read-only IMAP; new messages only) --"
  uv sync --group gmail --group dev >/dev/null
  uv run python scripts/ingest/05_workspace_gmail_imap_to_sqlite.py \
    --folder INBOX \
    --skip-duplicate-message-id \
    --since-days "$GMAIL_SINCE_DAYS"
  uv run python scripts/ingest/05_workspace_gmail_imap_to_sqlite.py \
    --folder "$GMAIL_SENT_FOLDER" \
    --skip-duplicate-message-id \
    --since-days "$GMAIL_SINCE_DAYS"
  echo ""
  echo "-- After ingest (canonical Gmail count) --"
  echo "$(_canonical_gmail_count_sql)"
else
  echo ""
  echo "-- Gmail ingest skipped (set RUN_GMAIL_INGEST=1 to fetch new mail) --"
fi

echo ""
echo "-- Incremental SQLite derived layers (no --rebuild) --"
uv sync --group dev >/dev/null
if [[ "$DASHBOARD_FAST" == "1" ]]; then
  echo "-- Fast mode preflight: validating full mart baseline health --"
  FAST_HEALTH="$(_fast_mode_mart_health_check)"
  FAST_CONTACT_COUNT="$(echo "$FAST_HEALTH" | awk -F'|' '{print $1}')"
  FAST_ORG_COUNT="$(echo "$FAST_HEALTH" | awk -F'|' '{print $2}')"
  FAST_OPP_COUNT="$(echo "$FAST_HEALTH" | awk -F'|' '{print $3}')"
  FAST_CANONICAL_COUNT="$(echo "$FAST_HEALTH" | awk -F'|' '{print $4}')"
  echo "  contact_master rows:      ${FAST_CONTACT_COUNT}"
  echo "  organization_master rows: ${FAST_ORG_COUNT}"
  echo "  opportunity_signals rows: ${FAST_OPP_COUNT}"
  echo "  canonical Gmail rows:     ${FAST_CANONICAL_COUNT}"
  if [[ "${FAST_CONTACT_COUNT}" -le 1000 || "${FAST_ORG_COUNT}" -le 1000 || "${FAST_OPP_COUNT}" -le 0 || "${FAST_CANONICAL_COUNT}" -le 0 ]]; then
    echo "ERROR: Fast mode refused: full mart baseline is unhealthy. Run build_business_mart.py --rebuild." >&2
    exit 2
  fi
  echo "-- Fast mode: skipping build_business_mart.py to avoid partial writes on baseline mart tables --"
else
  uv run python scripts/mart/build_business_mart.py
fi
uv run python scripts/commercial/build_commercial_intel_v1.py

echo ""
echo "-- Canonical Gmail count after derived layers (before Postgres sync) --"
SQLITE_CANONICAL_AFTER="$(_canonical_gmail_count_sql)"
echo "$SQLITE_CANONICAL_AFTER"

echo ""
echo "-- Sync dashboard mirror → Render Postgres --"
if [[ "$DASHBOARD_FAST" == "1" ]]; then
  uv run alembic -c alembic.ini upgrade head
  uv run python scripts/sync/sync_dashboard_postgres_mirror.py \
    --allow-non-scratch-postgres \
    --only canonical \
    --include-equipment-opportunities \
    --include-warm-cases \
    --updated-by "${ORIGENLAB_SYNC_UPDATED_BY:-phase1-cloud-manual}" \
    --reason "${ORIGENLAB_SYNC_REASON:-Phase 6.6 dashboard-fast canonical mirror sync}" \
    --json-out /tmp/phase1_cloud_mirror_sync.json
else
  bash scripts/ops/sync_dashboard_mirror_to_cloud.sh
fi

echo ""
echo "-- Verify mirror (assert Render dashboard readiness) --"
uv run python scripts/qa/verify_dashboard_postgres_mirror.py \
  --assert-render-dashboard \
  --expect-equipment-count "$EXPECT_EQUIPMENT" \
  --json-out "$DASHBOARD_VERIFY_JSON"

COMMERCIAL_MIRROR_STATUS="skipped"
CATALOG_MIRROR_STATUS="skipped"
LEAD_RESEARCH_MIRROR_STATUS="skipped"
OUTBOUND_SIDECAR_MIRROR_STATUS="skipped"
W1_CLOUD_SYNC_STATUS="skipped"
GMAIL_INGEST_LABEL="off"
COMMERCIAL_MIRROR_LABEL="off"
CATALOG_MIRROR_LABEL="off"
LEAD_RESEARCH_MIRROR_LABEL="off"
OUTBOUND_SIDECAR_MIRROR_LABEL="off"
W1_CLOUD_SYNC_LABEL="off"
[[ "$RUN_GMAIL_INGEST" == "1" ]] && GMAIL_INGEST_LABEL="on"
[[ "$RUN_COMMERCIAL_DEAL_MIRROR" == "1" ]] && COMMERCIAL_MIRROR_LABEL="on"
[[ "$RUN_CATALOG_MIRROR" == "1" ]] && CATALOG_MIRROR_LABEL="on"
[[ "$RUN_LEAD_RESEARCH_MIRROR" == "1" ]] && LEAD_RESEARCH_MIRROR_LABEL="on"
[[ "$RUN_OUTBOUND_SIDECAR_MIRROR" == "1" ]] && OUTBOUND_SIDECAR_MIRROR_LABEL="on"
[[ "$RUN_W1_CLOUD_SYNC" == "1" ]] && W1_CLOUD_SYNC_LABEL="on"

if [[ "$RUN_COMMERCIAL_DEAL_MIRROR" == "1" ]]; then
  # shellcheck source=scripts/ops/_refresh_commercial_deal_mirror.sh
  source "${PIPE}/scripts/ops/_refresh_commercial_deal_mirror.sh"
  if run_commercial_deal_mirror_refresh "$PIPE" "$COMMERCIAL_VERIFY_JSON"; then
    COMMERCIAL_MIRROR_STATUS="ok"
  else
    COMMERCIAL_MIRROR_STATUS="failed"
    echo "ERROR: Refresh stopped — commercial deal mirror verify failed (dashboard mirror may still be OK)." >&2
    exit 1
  fi
fi

if [[ "$RUN_CATALOG_MIRROR" == "1" ]]; then
  # shellcheck source=scripts/ops/_refresh_catalog_mirror.sh
  source "${PIPE}/scripts/ops/_refresh_catalog_mirror.sh"
  if run_catalog_mirror_refresh "$PIPE" "$CATALOG_VERIFY_JSON"; then
    CATALOG_MIRROR_STATUS="ok"
  else
    CATALOG_MIRROR_STATUS="failed"
    echo "ERROR: Refresh stopped — catalog mirror verify failed (dashboard mirror may still be OK)." >&2
    exit 1
  fi
fi

if [[ "$RUN_LEAD_RESEARCH_MIRROR" == "1" ]]; then
  # shellcheck source=scripts/ops/_refresh_lead_research_mirror.sh
  source "${PIPE}/scripts/ops/_refresh_lead_research_mirror.sh"
  if run_lead_research_mirror_refresh "$PIPE" "$LEAD_RESEARCH_VERIFY_JSON"; then
    LEAD_RESEARCH_MIRROR_STATUS="ok"
  else
    LEAD_RESEARCH_MIRROR_STATUS="failed"
    echo "ERROR: Refresh stopped — lead research mirror verify failed." >&2
    exit 1
  fi
fi

if [[ "$RUN_OUTBOUND_SIDECAR_MIRROR" == "1" ]]; then
  # shellcheck source=scripts/ops/_refresh_outbound_sidecar_mirror.sh
  source "${PIPE}/scripts/ops/_refresh_outbound_sidecar_mirror.sh"
  lead_flag=0
  [[ "$RUN_LEAD_RESEARCH_MIRROR" == "1" ]] && lead_flag=1
  if run_outbound_sidecar_mirror_refresh "$PIPE" "$OUTBOUND_SIDECAR_VERIFY_JSON" "$lead_flag"; then
    OUTBOUND_SIDECAR_MIRROR_STATUS="ok"
  else
    OUTBOUND_SIDECAR_MIRROR_STATUS="failed"
    echo "ERROR: Refresh stopped — outbound sidecar mirror verify failed (dashboard may show stale suppressions)." >&2
    exit 1
  fi
fi

if [[ "$RUN_W1_CLOUD_SYNC" == "1" ]]; then
  echo ""
  echo "-- W1 institution-prospect cloud sync → Render persistent disk --"
  if bash "${PIPE}/scripts/ops/sync_institution_prospects_to_cloud.sh"; then
    W1_CLOUD_SYNC_STATUS="ok"
  else
    W1_CLOUD_SYNC_STATUS="failed"
    echo "WARNING: W1 cloud sync failed — local ChileCompra publication remains authoritative." >&2
    echo "         Render API will serve reduced_mode=true until next successful W1 sync." >&2
    echo "         (Non-fatal: not aborting refresh.)" >&2
  fi
fi

echo ""
echo "============================================================"
echo "Render dashboard mirror refresh complete."
echo ""
echo "Summary:"
echo "  Gmail ingest:              $GMAIL_INGEST_LABEL"
echo "  Dashboard fast mode:       ${DASHBOARD_FAST}"
echo "  SQLite canonical (before): $SQLITE_CANONICAL_BEFORE"
echo "  SQLite canonical (after):  $SQLITE_CANONICAL_AFTER"
echo "  Dashboard mirror verify:   $DASHBOARD_VERIFY_JSON"
echo "  Commercial deal mirror:    $COMMERCIAL_MIRROR_LABEL ($COMMERCIAL_MIRROR_STATUS)"
if [[ "$RUN_COMMERCIAL_DEAL_MIRROR" == "1" ]]; then
  echo "  Commercial verify JSON:    $COMMERCIAL_VERIFY_JSON"
fi
echo "  Catalog mirror:            $CATALOG_MIRROR_LABEL ($CATALOG_MIRROR_STATUS)"
if [[ "$RUN_CATALOG_MIRROR" == "1" ]]; then
  echo "  Catalog verify JSON:       $CATALOG_VERIFY_JSON"
  if [[ "$CATALOG_MIRROR_STATUS" == "ok" && -f "$CATALOG_VERIFY_JSON" ]]; then
    echo "  Catalog Postgres counts:"
    uv run python -c "
import json, sys
payload = json.load(open(sys.argv[1]))
counts = payload.get('postgres_counts') or {}
for key in ('products', 'supplier_offers', 'price_snapshots', 'commercial_history'):
    print(f'    {key}: {counts.get(key, \"—\")}')
" "$CATALOG_VERIFY_JSON"
  fi
fi
echo "  Lead research mirror:      $LEAD_RESEARCH_MIRROR_LABEL ($LEAD_RESEARCH_MIRROR_STATUS)"
if [[ "$RUN_LEAD_RESEARCH_MIRROR" == "1" ]]; then
  echo "  Lead research verify JSON: $LEAD_RESEARCH_VERIFY_JSON"
fi
echo "  Outbound sidecar mirror:     $OUTBOUND_SIDECAR_MIRROR_LABEL ($OUTBOUND_SIDECAR_MIRROR_STATUS)"
if [[ "$RUN_OUTBOUND_SIDECAR_MIRROR" == "1" ]]; then
  echo "  Outbound sidecar verify JSON: $OUTBOUND_SIDECAR_VERIFY_JSON"
  if [[ "$OUTBOUND_SIDECAR_MIRROR_STATUS" == "ok" && -f "$OUTBOUND_SIDECAR_VERIFY_JSON" ]]; then
    echo "  Outbound Postgres counts:"
    uv run python -c "
import json, sys
payload = json.load(open(sys.argv[1]))
pg = payload.get('postgres_counts') or {}
for key in ('email_suppression_total', 'bounce_suppressions', 'contacted_sidecar_distinct_emails'):
    print(f'    {key}: {pg.get(key, \"—\")}')
if payload.get('postgres_lead_segments'):
    lead = payload['postgres_lead_segments']
    print(f'    lead_blocked: {lead.get(\"lead_blocked\", \"—\")}')
    print(f'    lead_net_new_safe: {lead.get(\"lead_net_new_safe\", \"—\")}')
" "$OUTBOUND_SIDECAR_VERIFY_JSON"
  fi
fi
echo "  W1 institution-prospect:   $W1_CLOUD_SYNC_LABEL ($W1_CLOUD_SYNC_STATUS)"
echo "  Sends / outreach / deploy: not run (read-only refresh)"
echo ""
echo "Open dashboard and click Refresh."
echo "============================================================"
