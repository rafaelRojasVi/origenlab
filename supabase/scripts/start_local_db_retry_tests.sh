#!/usr/bin/env bash
# OrigenLab V2 — prove start_local_db.sh retries only a genuine image-pull failure, and only once.
#
# Each scenario runs the real script against a fake `supabase` placed first on PATH. The fake
# plays back a scripted output and exit code per `start` attempt and records every call, so each
# scenario asserts the exit code, the exact sequence of CLI calls (how many starts, and that
# `stop --no-backup` precedes a retry), and the diagnostic. An attempt the scenario does not
# script "succeeds" loudly, so an unwanted retry turns into a visible false pass.
#
# Needs no Docker and no network. Safe to run anywhere.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SCRIPT="$REPO_ROOT/supabase/scripts/start_local_db.sh"

PASS=0
FAIL=0
ok()  { echo "ok    $*"; PASS=$(( PASS + 1 )); }
bad() { echo "FAIL  $*"; FAIL=$(( FAIL + 1 )); }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/bin"
cat > "$WORK/bin/supabase" <<'FAKE'
#!/usr/bin/env bash
set -u
echo "$*" >> "$SCENARIO/calls"
if [[ "${1:-}" == "start" ]]; then
  n=$(( $(grep -c '^start' "$SCENARIO/calls") ))
  if [[ -f "$SCENARIO/start.$n.out" ]]; then
    cat "$SCENARIO/start.$n.out"
    exit "$(cat "$SCENARIO/start.$n.rc")"
  fi
  echo "UNSCRIPTED ATTEMPT $n: Started supabase local development setup."
  exit 0
fi
exit 0
FAKE
chmod +x "$WORK/bin/supabase"

PULL_FAIL='failed to pull docker image from all registries'
STARTED='Started supabase local development setup.'

# attempt <scenario> <n> <rc> <output>
attempt() {
  printf '%s\n' "$4" > "$WORK/$1/start.$2.out"
  echo "$3" > "$WORK/$1/start.$2.rc"
}

# run_case <scenario> <label> <expected rc: 0|nonzero> <expected calls, ';'-joined> <needle>
run_case() {
  local sc="$1" label="$2" want_rc="$3" want_calls="$4" needle="$5"
  local out rc=0 calls
  out="$(SCENARIO="$WORK/$sc" PATH="$WORK/bin:$PATH" OL_START_RETRY_DELAY=0 "$SCRIPT" 2>&1)" || rc=$?
  calls="$(paste -sd ';' "$WORK/$sc/calls" 2>/dev/null)"

  if [[ "$want_rc" == 0 && "$rc" -ne 0 ]] || [[ "$want_rc" != 0 && "$rc" -eq 0 ]]; then
    bad "$label: exit $rc, expected $want_rc"
  elif [[ "$calls" != "$want_calls" ]]; then
    bad "$label: calls were [$calls], expected [$want_calls]"
  elif [[ "$out" != *"$needle"* ]]; then
    bad "$label: output did not mention '$needle'"
    printf '      got: %s\n' "$(printf '%s' "$out" | tail -n 2)"
  else
    ok "$label"
  fi
}

new() { mkdir -p "$WORK/$1"; }

GENUINE_MIGRATION=$'Applying migration 20260901000000_crm.sql...\nERROR: relation "crm.party" does not exist (SQLSTATE 42P01)\nAt statement 3: alter table crm.party ...\nTry rerunning the command with --debug to troubleshoot the error.\nerror running container: exit 1'
GENUINE_HEALTH=$'Starting containers...\nWaiting for health checks...\nsupabase_db_origenlab container is not ready: unhealthy\nTry rerunning the command with --debug to troubleshoot the error.'
PULL_TAIL=$'Pulling images...\nerror pulling image: toomanyrequests: rate limit exceeded\nRetrying after 4s: public.ecr.aws/supabase/postgres:17\n'"$PULL_FAIL"$'\nTry rerunning the command with --debug to troubleshoot the error.'
# Registry noise the CLI recovered from, including the exact phrase, then a genuine failure
# whose own output fills the final five lines.
NOISE_THEN_GENUINE=$'Pulling images...\nerror pulling image: toomanyrequests: rate limit exceeded\nRetrying after 4s: public.ecr.aws/supabase/postgres:17\ndownload failed: connection reset by peer\n'"$PULL_FAIL"$' (mirror 1)\nRetrying after 8s: ghcr.io/supabase/postgres:17\nStarting database...\n'"$GENUINE_MIGRATION"

# 1. success on the first attempt
new s1
attempt s1 1 0 "$STARTED"
run_case s1 "success on first attempt" 0 "start" "$STARTED"

# 2. image-pull failure, then success
new s2
attempt s2 1 1 "$PULL_TAIL"
attempt s2 2 0 "$STARTED"
run_case s2 "image-pull failure then success" 0 "start;stop --no-backup;start" "retrying once"

# 3. genuine failure on the first attempt
new s3
attempt s3 1 1 "$GENUINE_HEALTH"
run_case s3 "genuine failure on first attempt" 1 "start" "refusing to retry"

# 4. image-pull failure, then a genuine failure
new s4
attempt s4 1 1 "$PULL_TAIL"
attempt s4 2 1 "$GENUINE_MIGRATION"
run_case s4 "image-pull failure then genuine failure" 1 "start;stop --no-backup;start" "attempt 2"

# 5. persistent image-pull failure: exactly two attempts, never a third
new s5
attempt s5 1 1 "$PULL_TAIL"
attempt s5 2 1 "$PULL_TAIL"
attempt s5 3 1 "$PULL_TAIL"
run_case s5 "persistent image-pull failure" 1 "start;stop --no-backup;start" "on 2 attempts"

# 6. misleading pull-related noise followed by a genuine failure
new s6
attempt s6 1 1 "$NOISE_THEN_GENUINE"
run_case s6 "pull noise followed by genuine failure" 1 "start" "refusing to retry"

# 7. genuine failure followed by what would be an apparent success
new s7
attempt s7 1 1 "$GENUINE_MIGRATION"
attempt s7 2 0 "$STARTED"
run_case s7 "genuine failure followed by apparent success" 1 "start" "refusing to retry"

echo
echo "start_local_db retry: $PASS passed, $FAIL failed"
(( FAIL == 0 ))
