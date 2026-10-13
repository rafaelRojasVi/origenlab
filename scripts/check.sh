#!/usr/bin/env bash
# `make check` / `make check-all`: run each needed target, stop at the first failure, print
# how long each took so a slow suite is visible the day it gets slow.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"

mapfile -t targets < <(python3 scripts/changed_apps.py "$@")
if [[ ${#targets[@]} -eq 0 ]]; then
  echo "No app changed vs origin/main; nothing to test."
  exit 0
fi
echo "targets: ${targets[*]}"

summary=()
for t in "${targets[@]}"; do
  start=$SECONDS
  printf '\n== %s ==\n' "$t"
  if ! make --no-print-directory "test-$t"; then
    echo "FAILED: $t after $((SECONDS - start)) s" >&2
    exit 1
  fi
  summary+=("$(printf '%-16s %4d s' "$t" $((SECONDS - start)))")
done
printf '\n== passed ==\n'
printf '%s\n' "${summary[@]}"
