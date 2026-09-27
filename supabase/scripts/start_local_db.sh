#!/usr/bin/env bash
# OrigenLab V2 — start the local Supabase stack for CI, retrying once on a registry pull failure.
#
# At most two attempts: the first, and one retry. The retry is taken only when the *final* lines
# of the failed attempt say the CLI could not pull an image from any registry. Registry noise
# earlier in the log (toomanyrequests, "Retrying after", "download failed") is ignored: the CLI
# prints those while it recovers on its own, and a later genuine migration, health-check or
# startup failure must not be mistaken for a transient one. Any other failure exits at once, so
# a genuine failure can never be followed by a retry that exits 0.
#
# Environment (for the test harness, supabase/scripts/start_local_db_retry_tests.sh):
#   OL_START_RETRY_DELAY   seconds to wait before the retry (default 20)

set -uo pipefail

readonly PULL_FAILURE='failed to pull docker image from all registries'
readonly TAIL_LINES=5
readonly MAX_ATTEMPTS=2
delay="${OL_START_RETRY_DELAY:-20}"

log="$(mktemp)"
trap 'rm -f "$log"' EXIT

for (( attempt = 1; attempt <= MAX_ATTEMPTS; attempt++ )); do
  : > "$log"
  rc=0
  supabase start 2>&1 | tee "$log" || rc=$?

  if (( rc == 0 )); then
    exit 0
  fi

  if ! tail -n "$TAIL_LINES" "$log" | grep -Fqi -- "$PULL_FAILURE"; then
    echo "::error::supabase start failed on attempt $attempt (exit $rc) for a reason other than an image pull; refusing to retry"
    exit 1
  fi

  if (( attempt == MAX_ATTEMPTS )); then
    echo "::error::supabase start could not pull images from any registry on $MAX_ATTEMPTS attempts"
    exit 1
  fi

  echo "::warning::supabase start could not pull images on attempt $attempt; stopping the stack and retrying once in ${delay}s"
  supabase stop --no-backup || echo "::warning::supabase stop --no-backup exited non-zero before the retry"
  sleep "$delay"
done

# Unreachable: every path through the loop exits.
exit 1
