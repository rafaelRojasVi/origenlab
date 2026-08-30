#!/usr/bin/env bash
# Manual SQLite → cloud Postgres dashboard mirror (Phase 1).
# SAFETY: Read-only SQLite; writes cloud Postgres mirror only. No Gmail ingest, no mart rebuild.
# PHASE W1 (2026-08): legacy commercial.equipment_opportunity* is no longer synced
# here — it is frozen for observation. See docs/architecture/
# EQUIPMENT_READ_MODEL_BOUNDARY.md for the explicit manual/backfill reload path.
set -euo pipefail

PIPE="$(cd "$(dirname "$0")/../.." && pwd)"

# shellcheck source=scripts/ops/_load_pipeline_dotenv.sh
source "${PIPE}/scripts/ops/_load_pipeline_dotenv.sh"
load_pipeline_dotenv "$PIPE"

: "${ORIGENLAB_SQLITE_PATH:?Set ORIGENLAB_SQLITE_PATH to local canonical SQLite (not uploaded)}"
: "${ORIGENLAB_CLOUD_POSTGRES_URL:?Set ORIGENLAB_CLOUD_POSTGRES_URL to cloud external Postgres URL}"

# shellcheck source=scripts/ops/_cloud_postgres_env.sh
source "${PIPE}/scripts/ops/_cloud_postgres_env.sh"
cloud_postgres_prepare_env "$PIPE"

cd "$PIPE"
uv sync --group dev >/dev/null

echo "== Phase 1 cloud mirror sync =="
echo "SQLite: ${ORIGENLAB_SQLITE_PATH}"
echo "Postgres: ${HOST_DB}"

uv run alembic -c alembic.ini upgrade head

uv run python scripts/sync/sync_dashboard_postgres_mirror.py \
  --allow-non-scratch-postgres \
  --include-warm-cases \
  --updated-by "${ORIGENLAB_SYNC_UPDATED_BY:-phase1-cloud-manual}" \
  --reason "${ORIGENLAB_SYNC_REASON:-Phase 1 cloud dashboard mirror manual sync}" \
  --json-out /tmp/phase1_cloud_mirror_sync.json

uv run python scripts/qa/verify_dashboard_postgres_mirror.py --json-out /tmp/phase1_cloud_mirror_verify.json

echo "Sync JSON: /tmp/phase1_cloud_mirror_sync.json"
echo "Verify JSON: /tmp/phase1_cloud_mirror_verify.json"
