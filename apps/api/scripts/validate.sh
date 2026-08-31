#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

# Render-style runtime install: catch missing runtime deps (e.g. psycopg)
# before deploy. Must match apps/api/Dockerfile's install command exactly
# (see test_drive_deployment_dependency_audit.py) -- the production image
# is Drive-capable (CRM-Q1B, --extra drive).
uv sync --frozen --no-dev --extra drive
uv run --no-sync python - <<'PY'
import psycopg
import google.oauth2.credentials
import google.oauth2.service_account
import origenlab_api.main
print("ok: apps/api no-dev runtime imports (incl. drive extra)")
PY

uv run --no-sync python scripts/check_runtime_dependency_boundary.py

uv sync --group dev --frozen
ORIGENLAB_API_BACKEND=sqlite \
ORIGENLAB_POSTGRES_URL= \
ALEMBIC_DATABASE_URL= \
uv run --frozen pytest tests -q
