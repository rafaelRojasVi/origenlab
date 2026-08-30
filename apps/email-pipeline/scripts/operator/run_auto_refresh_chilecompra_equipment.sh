#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

UV_BIN="${ORIGENLAB_UV_BIN:-/home/rafael/.local/bin/uv}"

"$UV_BIN" run origenlab auto-refresh-chilecompra-equipment \
  --once \
  --apply \
  --publish-institution-prospects \
  --no-publish-read-model
