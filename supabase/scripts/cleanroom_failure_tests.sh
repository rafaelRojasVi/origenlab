#!/usr/bin/env bash
# OrigenLab V2 — failure injection for the clean-room tooling.
#
# Proves that cleanroom_db.sh and its guard refuse what they claim to refuse. A guard that has
# never been made to fail is a guard nobody has tested — and this one stands between a `drop
# database` and `origenlab_dev`.
#
# Every scenario asserts a non-zero exit and a diagnostic that names the real reason.
#
# Safe to run at any time. It creates nothing, drops nothing, and never connects to
# `origenlab_dev` or `origenlab_clean` — every case below refuses before a connection is opened.
#
# Procedure: docs/OPERATIONS.md §4.1 (“The clean-room database”).

set -uo pipefail

OL_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
export OL_REPO_ROOT
cd "$OL_REPO_ROOT"

PASS=0
FAIL=0

ok()  { echo "ok    $*"; PASS=$(( PASS + 1 )); }
bad() { echo "FAIL  $*"; FAIL=$(( FAIL + 1 )); }

# expect_refusal <label> <needle> <command...>
expect_refusal() {
  local label="$1" needle="$2"; shift 2
  local out rc=0
  out="$("$@" 2>&1)" || rc=$?
  if (( rc == 0 )); then
    bad "$label: exited 0, expected a refusal"
    return
  fi
  if [[ "$out" != *"$needle"* ]]; then
    bad "$label: refused, but the diagnostic did not mention '$needle'"
    printf '      got: %s\n' "$(printf '%s' "$out" | tail -n 3)"
    return
  fi
  ok "$label"
}

# expect_success <label> <needle> <command...>
expect_success() {
  local label="$1" needle="$2"; shift 2
  local out rc=0
  out="$("$@" 2>&1)" || rc=$?
  if (( rc != 0 )); then
    bad "$label: exited $rc, expected success"
    printf '      got: %s\n' "$(printf '%s' "$out" | tail -n 3)"
    return
  fi
  if [[ "$out" != *"$needle"* ]]; then
    bad "$label: succeeded, but the output did not mention '$needle'"
    return
  fi
  ok "$label"
}

# A shell that sources the guard library and runs one expression.
guard() { bash -c '. supabase/scripts/lib/local_target.sh; '"$1"; }

echo "== A-C: the clean-room name is a constant, not an input =="

expect_refusal "A: a tampered name constant is refused before anything else" \
  "not 'origenlab_clean'" \
  guard 'OL_CLEAN_DBNAME=origenlab_dev; ol_require_cleanroom_database'

expect_refusal "B: an empty name constant is refused" \
  "not 'origenlab_clean'" \
  guard 'OL_CLEAN_DBNAME=""; ol_require_cleanroom_database'

expect_refusal "C: a name that would need quoting is refused" \
  "not 'origenlab_clean'" \
  guard 'OL_CLEAN_DBNAME="origenlab_clean; drop database origenlab_dev"; ol_require_cleanroom_database'

echo ""
echo "== D-F: no psql wrapper connects before its guard has run =="

expect_refusal "D: ol_psql_clean refuses before the guard" \
  "called before ol_require_cleanroom_database succeeded" \
  guard 'ol_psql_clean -c "select 1"'

expect_refusal "E: ol_psql_clean_maintenance refuses before the guard" \
  "called before ol_require_cleanroom_database succeeded" \
  guard 'ol_psql_clean_maintenance -c "select 1"'

expect_refusal "F: the guard discards the development DSN, so ol_psql_dev cannot connect" \
  "called before ol_require_dev_database succeeded" \
  guard 'ol_require_cleanroom_database >/dev/null 2>&1; ol_psql_dev -c "select 1"'

echo ""
echo "== G-I: the container must be this working tree's, and loopback-only =="

expect_refusal "G: a foreign working tree is refused" \
  "not found; refusing to connect" \
  guard 'ol_require_cleanroom_database /nonexistent-working-tree'

expect_refusal "H: a linked project is refused" \
  "linked to a hosted project" \
  bash -c '
    tmp="$(mktemp -d)"; trap "rm -rf $tmp" EXIT
    mkdir -p "$tmp/supabase/.temp"
    cp supabase/config.toml "$tmp/supabase/config.toml"
    echo ref > "$tmp/supabase/.temp/project-ref"
    . supabase/scripts/lib/local_target.sh
    ol_require_cleanroom_database "$tmp"'

expect_refusal "I: a config naming another project is refused" \
  "project_id is" \
  bash -c '
    tmp="$(mktemp -d)"; trap "rm -rf $tmp" EXIT
    mkdir -p "$tmp/supabase"
    sed "s/^project_id = .*/project_id = \"somebody-else\"/" supabase/config.toml > "$tmp/supabase/config.toml"
    . supabase/scripts/lib/local_target.sh
    ol_require_cleanroom_database "$tmp"'

echo ""
echo "== J-M: the build refuses rather than half-building =="

expect_refusal "J: build refuses an unknown option" \
  "unknown option" \
  supabase/scripts/cleanroom_db.sh build --database origenlab_dev

expect_refusal "K: there is no way to name the database on the command line" \
  "unknown option" \
  supabase/scripts/cleanroom_db.sh build origenlab_dev

expect_refusal "L: an absent migration root refuses before anything is created" \
  "does not exist" \
  env OL_MIGRATION_ROOT=/nonexistent-migration-root supabase/scripts/cleanroom_db.sh build --force

expect_refusal "M: an absent Gmail manifest refuses before anything is created" \
  "does not exist" \
  env OL_LOCAL_PRIVATE_ROOT=/nonexistent-private-root supabase/scripts/cleanroom_db.sh build --force

echo ""
echo "== N-O: destructive actions need to be asked for =="

expect_refusal "N: drop refuses without --force" \
  "re-run with --force" \
  supabase/scripts/cleanroom_db.sh drop

expect_refusal "O: an unknown subcommand is refused" \
  "unknown subcommand" \
  supabase/scripts/cleanroom_db.sh reset

echo ""
echo "== P-T: the expected-state comparison is exact in both directions =="
#
# The fixtures come from supabase/cleanroom/emit_expected_for_tests.py, which renders the
# declared baseline as a probe stream and lets a case bend one value. `cleanroom_db.sh` never
# calls it: a verification that could be handed its own expected answer would prove nothing.

emit() { python3 supabase/cleanroom/emit_expected_for_tests.py "$@"; }
check() { emit "$@" | python3 supabase/cleanroom/compare.py; }

expect_success "P: the declared baseline passes against itself" \
  "38 probes, all exact" \
  check

expect_refusal "Q: a probe declared but never measured fails" \
  "source_record.total: declared but never measured" \
  check --drop source_record.total

expect_refusal "R: a probe measured but not declared fails" \
  "something.undeclared: measured but not declared" \
  check --add something.undeclared=1

expect_refusal "S: a single drifted count fails, and names it" \
  "source_record.gmail_message: expected 31, measured 27" \
  check source_record.gmail_message=27

expect_refusal "T: a correct total cannot hide a wrong breakdown" \
  "source_record.migration_manifest: expected 4, measured 0" \
  check source_record.gmail_message=35 source_record.migration_manifest=0

echo ""
echo "== U-X: the residue that started all this is caught by name =="

expect_refusal "U: seven fixture source records are caught" \
  "source_record.pytest_residue: expected 0, measured 7" \
  check source_record.pytest_residue=7

expect_refusal "V: seven fixture operators are caught" \
  "platform.operator.pytest_residue: expected 0, measured 7" \
  check platform.operator.pytest_residue=7

expect_refusal "W: nine command receipts are caught" \
  "platform.command_receipt: expected 0, measured 9" \
  check platform.command_receipt=9

expect_refusal "X: sixteen command-driven domain events are caught" \
  "crm.domain_event.with_command_receipt: expected 0, measured 16" \
  check crm.domain_event.with_command_receipt=16

expect_refusal "Y: a send flag left on is caught" \
  "outbound.send_control.marketing_enabled: expected 0, measured 1" \
  check outbound.send_control.marketing_enabled=1

echo ""
echo "─────────────────────────────────────────────"
echo "$PASS passed, $FAIL failed"
(( FAIL == 0 ))
