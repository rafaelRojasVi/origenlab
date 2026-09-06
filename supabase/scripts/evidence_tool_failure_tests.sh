#!/usr/bin/env bash
# OrigenLab V2 — Slice 0 failure-injection checks for the evidence tooling.
#
# The replay procedure and the target guard are themselves evidence, so they are tested the way any
# other assertion is: by making them fail on purpose and proving they say so. Each scenario runs the
# real supabase/scripts/replay_evidence.sh (or the guard) against temporary command shims placed
# first on PATH; the shims answer for the Supabase CLI deterministically and never touch the
# network. `psql` is shimmed too, only to record whether it was invoked at all.
#
# Nothing here contacts a hosted project or a remote host: the only addresses used are the loopback
# target the local stack already publishes and 192.0.2.10, the RFC 5737 documentation address, which
# is never a real host and is never actually dialled — scenario D proves the guard refuses before
# any connection is attempted.
#
# Preconditions: the local stack is running and fully migrated (`supabase start`,
# `supabase db reset --local`), because scenarios that get past the dump take a real catalogue
# snapshot. Requires bash and psql; installs nothing.
#
# Procedure: docs/OPERATIONS.md §4.1.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

REPLAY="$ROOT/supabase/scripts/replay_evidence.sh"
GUARD="$ROOT/supabase/scripts/lib/local_target.sh"

OL_REAL_SUPABASE="$(command -v supabase)" || { echo "FAIL: supabase CLI not found" >&2; exit 2; }
OL_REAL_PSQL="$(command -v psql)"         || { echo "FAIL: psql not found" >&2; exit 2; }
export OL_REAL_SUPABASE OL_REAL_PSQL

WORK="$(mktemp -d)"
# Scratch space. Set OL_KEEP_WORK=1 to keep the captured logs for inspection after a failure.
keep_or_clean() {
  if [[ -n "${OL_KEEP_WORK:-}" ]]; then echo "work dir kept: $WORK"; else rm -rf "$WORK"; fi
}
trap keep_or_clean EXIT
SHIM="$WORK/shim"
STATE="$WORK/state"
mkdir -p "$SHIM" "$STATE"
export OL_SHIM_STATE="$STATE"

PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); printf 'ok    %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf 'not ok %s  [%s]\n' "$1" "${2:-}"; }
check() { # description  condition-already-evaluated-as-rc  detail
  if [[ "$2" == 0 ]]; then ok "$1"; else bad "$1" "${3:-}"; fi
}

# ---------------------------------------------------------------------------------------------
# Fixtures the shims serve.
# ---------------------------------------------------------------------------------------------

# A dump that satisfies every completeness assertion: 7 CREATE SCHEMA, 32 CREATE TABLE,
# 122 CREATE POLICY. Its content is irrelevant; only the shape the assertions count is.
{
  for i in $(seq 1 7);   do echo "CREATE SCHEMA s$i;"; done
  for i in $(seq 1 32);  do echo "CREATE TABLE s1.t$i (id integer);"; done
  for i in $(seq 1 122); do echo "CREATE POLICY p$i ON s1.t1 FOR SELECT TO r USING (true);"; done
} >"$STATE/schema_good.sql"

# Two applied-migration listings. Both name every local migration, so both pass the completeness
# assertion; they differ in one field, so only the equality assertion can catch it.
versions=()
for f in "$ROOT"/supabase/migrations/*.sql; do v="$(basename "$f")"; versions+=("${v%%_*}"); done
{
  printf '{"migrations":['
  sep=""
  for v in "${versions[@]}"; do printf '%s{"local":"%s","remote":"%s"}' "$sep" "$v" "$v"; sep=","; done
  printf '],"message":"Migrations listed"}\n'
} >"$STATE/migrations_a.txt"
sed 's/"Migrations listed"/"Migrations listed (second run reported a different state)"/' \
  "$STATE/migrations_a.txt" >"$STATE/migrations_b.txt"

# ---------------------------------------------------------------------------------------------
# The shims.
# ---------------------------------------------------------------------------------------------
cat >"$SHIM/supabase" <<'SHIM_EOF'
#!/usr/bin/env bash
set -uo pipefail
inject="${OL_INJECT:-none}"
real="${OL_REAL_SUPABASE:?}"
state="${OL_SHIM_STATE:?}"

cmd="${1:-}"; shift || true
case "$cmd" in
  status)
    if [[ "$inject" == status_fail ]]; then
      echo "supabase status: no local project containers are running (injected)" >&2
      exit 1
    fi
    exec "$real" status "$@"
    ;;
  migration)
    if [[ "${1:-}" == "list" ]]; then
      run="$(cat "$state/run" 2>/dev/null || echo 0)"
      echo "Connecting to local database..." >&2
      if [[ "$inject" == migrations_differ && "$run" == 2 ]]; then
        cat "$state/migrations_b.txt"
      else
        cat "$state/migrations_a.txt"
      fi
      exit 0
    fi
    exec "$real" migration "$@"
    ;;
  db)
    sub="${1:-}"; shift || true
    case "$sub" in
      reset)
        run="$(( $(cat "$state/run" 2>/dev/null || echo 0) + 1 ))"
        echo "$run" >"$state/run"
        if [[ "$inject" == reset_fail ]]; then
          echo "Recreating database..."
          echo "Applying migration 20260905230800_slice0_foundation_schemas_default_privileges.sql..."
          echo "failed to apply migration: injected reset failure" >&2
          exit 1
        fi
        echo "Recreating database..."
        echo "Applying migration (shim: no-op, the live database is left as it is)"
        echo "Finished supabase db reset."
        exit 0
        ;;
      dump)
        out=""; prev=""
        for a in "$@"; do
          if [[ "$prev" == "-f" || "$prev" == "--file" ]]; then out="$a"; fi
          prev="$a"
        done
        echo "Dumping schemas from local database..." >&2
        if [[ -z "$out" ]]; then echo "shim: no -f target given" >&2; exit 1; fi
        case "$inject" in
          dump_fail)  echo "failed to dump schemas: injected dump failure" >&2; exit 1 ;;
          dump_empty) : >"$out"; echo "Dumped schema to $out." >&2; exit 0 ;;
          *)          cp "$state/schema_good.sql" "$out"; echo "Dumped schema to $out." >&2; exit 0 ;;
        esac
        ;;
      *) exec "$real" db "$sub" "$@" ;;
    esac
    ;;
  *) exec "$real" "$cmd" "$@" ;;
esac
SHIM_EOF

cat >"$SHIM/psql" <<'SHIM_EOF'
#!/usr/bin/env bash
printf '%s\n' "invoked" >>"${OL_SHIM_STATE:?}/psql_calls"
exec "${OL_REAL_PSQL:?}" "$@"
SHIM_EOF

chmod +x "$SHIM/supabase" "$SHIM/psql"

run_replay() { # $1 = inject, $2 = log file ; env-only overrides, PATH shimmed
  rm -f "$STATE/run" "$STATE/psql_calls"
  local out; out="$WORK/out_$1"; mkdir -p "$out"
  local rc=0
  env PATH="$SHIM:$PATH" OL_INJECT="$1" "$REPLAY" "$out" >"$2" 2>&1 || rc=$?
  echo "$rc"
}

# A conclusion is the standalone word PASS. Matching the bare substring would also hit
# "PGPASSWORD" in the scrub notice, so the pattern is anchored on word boundaries.
no_pass_line() { ! grep -qE '(^|[[:space:]])PASS([[:space:]]|$)' "$1"; }

echo "== evidence-tool failure injection =="
echo

# --- control: with no injection the procedure still concludes PASS -----------------------------
log="$WORK/log_control"
rc="$(run_replay none "$log")"
check "control: with no injection the replay procedure exits 0" "$([[ $rc -eq 0 ]] && echo 0 || echo 1)" "rc=$rc"
check "control: a PASS conclusion is printed" "$(grep -q '^PASS replay evidence' "$log" && echo 0 || echo 1)" "no PASS line"

# --- A: simulated reset failure ---------------------------------------------------------------
log="$WORK/log_A"
rc="$(run_replay reset_fail "$log")"
check "A: a failed \`supabase db reset --local\` makes the replay procedure exit non-zero" \
  "$([[ $rc -ne 0 ]] && echo 0 || echo 1)" "rc=$rc"
check "A: no PASS line is printed" "$(no_pass_line "$log" && echo 0 || echo 1)" "PASS found"
check "A: the reset's exit status is reported" \
  "$(grep -q 'supabase db reset --local exited' "$log" && echo 0 || echo 1)" "no diagnostic"

# --- B: simulated dump failure ------------------------------------------------------------------
log="$WORK/log_B"
rc="$(run_replay dump_fail "$log")"
check "B: a failed \`supabase db dump --local\` makes the replay procedure exit non-zero" \
  "$([[ $rc -ne 0 ]] && echo 0 || echo 1)" "rc=$rc"
check "B: no PASS line is printed" "$(no_pass_line "$log" && echo 0 || echo 1)" "PASS found"
check "B: the dump's stderr is surfaced, not discarded" \
  "$(grep -q 'injected dump failure' "$log" && echo 0 || echo 1)" "stderr was swallowed"

# --- C: a successful but empty dump ---------------------------------------------------------------
log="$WORK/log_C"
rc="$(run_replay dump_empty "$log")"
check "C: a successful but empty dump fails a completeness assertion" \
  "$([[ $rc -ne 0 ]] && echo 0 || echo 1)" "rc=$rc"
check "C: no PASS line is printed" "$(no_pass_line "$log" && echo 0 || echo 1)" "PASS found"
check "C: the failure names the completeness assertion, not a hash mismatch" \
  "$(grep -qE 'schema dump is empty|CREATE (TABLE|POLICY) statements, expected' "$log" && echo 0 || echo 1)" "wrong diagnostic"

# --- D: failed local-status discovery with a hostile libpq environment ----------------------------
# The guard must refuse before psql is invoked at all. 192.0.2.10 is RFC 5737 documentation space.
for target in "$REPLAY" "$ROOT/supabase/scripts/verify_direct_logins.sh"; do
  name="$(basename "$target")"
  log="$WORK/log_D_$name"
  rm -f "$STATE/run" "$STATE/psql_calls"
  rc=0
  env PATH="$SHIM:$PATH" OL_INJECT=status_fail \
      PGHOST=192.0.2.10 PGPORT=5432 PGUSER=postgres PGDATABASE=postgres PGPASSWORD=hostile \
      "$target" "$WORK/out_D_$name" >"$log" 2>&1 || rc=$?
  check "D ($name): a failed \`supabase status\` with hostile PG* variables makes the script exit non-zero" \
    "$([[ $rc -ne 0 ]] && echo 0 || echo 1)" "rc=$rc"
  check "D ($name): psql is never invoked — no connection is attempted after validation fails" \
    "$([[ ! -s "$STATE/psql_calls" ]] && echo 0 || echo 1)" "psql was invoked $(cat "$STATE/psql_calls" 2>/dev/null | wc -l) time(s)"
  check "D ($name): the inherited libpq environment is reported as ignored, never used as a fallback" \
    "$(grep -q 'ignoring inherited libpq environment' "$log" && echo 0 || echo 1)" "no scrub notice"
  check "D ($name): no PASS line is printed" "$(no_pass_line "$log" && echo 0 || echo 1)" "PASS found"
done

# --- E: unequal applied-migration lists ------------------------------------------------------------
log="$WORK/log_E"
rc="$(run_replay migrations_differ "$log")"
check "E: two runs whose applied-migration lists differ make the replay procedure exit non-zero" \
  "$([[ $rc -ne 0 ]] && echo 0 || echo 1)" "rc=$rc"
check "E: the failure names the applied-migration list" \
  "$(grep -q 'applied-migration list differs' "$log" && echo 0 || echo 1)" "wrong diagnostic"
check "E: no PASS line is printed" "$(no_pass_line "$log" && echo 0 || echo 1)" "PASS found"

echo
echo "evidence-tool failure injection: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
