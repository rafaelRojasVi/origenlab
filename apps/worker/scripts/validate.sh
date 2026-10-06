#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

# Render-style runtime install first: the cron service builds with `uv sync --frozen --no-dev`.
# A runtime import that only the dev group satisfies fails here, not on Render.
uv sync --frozen --no-dev
uv run --no-sync python - <<'PY'
import boto3
import psycopg
import origenlab_worker.v1_reuse
import origenlab_worker.drive_filing
import origenlab_worker.cli
print("ok: apps/worker no-dev runtime imports")
PY

uv sync --group dev --frozen
uv run --frozen pytest tests -q
