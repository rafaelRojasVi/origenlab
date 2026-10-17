#!/usr/bin/env bash
# `make lint`: the public-repo hygiene scan, then every pre-commit hook on every tracked file —
# the same run CI does (lint.yml).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if ! command -v pre-commit >/dev/null 2>&1; then
  echo "FAIL: pre-commit is not installed. Run 'make setup' (or: uv tool install pre-commit)." >&2
  exit 1
fi
# The public-repo hygiene scan (tracked .env/*.pem/*.jsonl/*.sqlite, docs/client, hosted ids,
# workflow permissions) runs here too, so the always-required lint workflow gates every release;
# the supabase workflow, which also runs it, is not required for most releases.
scripts/security/check-public-repo-hygiene.sh
exec pre-commit run --all-files --show-diff-on-failure "$@"
