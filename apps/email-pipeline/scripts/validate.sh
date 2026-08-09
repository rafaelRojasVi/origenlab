#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

./scripts/sync_test_env.sh

uv run pytest \
  tests/test_operator_cli.py \
  tests/test_operator_status_report.py \
  tests/test_daily_core_manifest.py \
  tests/test_daily_core_docs.py \
  tests/test_script_map_docs.py \
  tests/test_module_facade_audit.py \
  -q

uv run origenlab status
uv run origenlab daily-core
uv run origenlab daily-core --help
uv run origenlab refresh-dashboard
uv run origenlab audit-facades -- --fail-on-manual-review
