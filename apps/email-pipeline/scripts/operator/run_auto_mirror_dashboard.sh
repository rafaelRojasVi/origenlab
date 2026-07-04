#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

UV_BIN="${ORIGENLAB_UV_BIN:-/home/rafael/.local/bin/uv}"
OPERATOR="${ORIGENLAB_OPERATOR_NAME:-rafael}"

"$UV_BIN" run --group postgres origenlab auto-mirror-dashboard \
  --once \
  --apply \
  --operator "$OPERATOR" \
  --reason auto_dashboard_mirror_after_successful_daily_core \
  --allow-non-scratch-postgres
