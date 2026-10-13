#!/usr/bin/env bash
# `make lint`: every pre-commit hook on every tracked file — the same run CI does (lint.yml).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if ! command -v pre-commit >/dev/null 2>&1; then
  echo "FAIL: pre-commit is not installed. Run 'make setup' (or: uv tool install pre-commit)." >&2
  exit 1
fi
exec pre-commit run --all-files --show-diff-on-failure "$@"
