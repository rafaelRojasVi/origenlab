#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Canonical way to run the apps/email-pipeline suite.
#
#   ./scripts/test.sh              # full suite
#   ./scripts/test.sh tests/x.py   # focused run
#
# Syncs the required dependency groups first so the suite cannot silently
# under-collect. Use this instead of a bare `uv run pytest`.
# -----------------------------------------------------------------------------
set -euo pipefail

cd "$(dirname "$0")/.."

./scripts/sync_test_env.sh

if [ "$#" -eq 0 ]; then
  exec uv run pytest -q
fi

exec uv run pytest "$@"
