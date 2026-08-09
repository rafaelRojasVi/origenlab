#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Canonical test-environment bootstrap for apps/email-pipeline.
#
# `uv sync` prunes the venv to exactly the groups it is given, so syncing a
# narrower set (for example `--group dev --group gmail`) silently uninstalls
# pandas/openai and makes a later `uv run pytest -q` under-collect. Every
# entry point that prepares the suite must go through this one script.
# -----------------------------------------------------------------------------
set -euo pipefail

cd "$(dirname "$0")/.."

# Superset of the groups CI installs (see .github/workflows/email-pipeline.yml)
# plus `gmail`, whose tests use importorskip and therefore only ever add cases.
exec uv sync \
  --group dev \
  --group gmail \
  --group data-tools \
  --group postgres \
  --group lab \
  --frozen "$@"
