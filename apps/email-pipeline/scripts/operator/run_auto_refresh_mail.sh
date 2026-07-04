#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/../.."

UV_BIN="${ORIGENLAB_UV_BIN:-/home/rafael/.local/bin/uv}"

"$UV_BIN" run --group gmail origenlab auto-refresh-mail --once --apply
