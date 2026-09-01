#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

section() {
  printf "\n== %s ==\n" "$1"
}

section "web"
cd "$ROOT_DIR/apps/web"
npm ci
npm run check
npm run validate:catalog
npm run build

section "email-pipeline"
cd "$ROOT_DIR/apps/email-pipeline"
uv sync --group dev --group data-tools --group postgres --group lab --frozen
uv run pytest tests -q
uv run origenlab refresh-dashboard

section "api"
cd "$ROOT_DIR/apps/api"
uv sync --group dev --frozen
uv run pytest tests -q

section "dashboard"
cd "$ROOT_DIR/apps/dashboard"
npm ci
npm test
npm run build

section "dashboard-proxy"
cd "$ROOT_DIR/apps/dashboard-proxy"
npm ci
npm run validate
npm run typecheck

section "done"
echo "All monorepo checks passed."
