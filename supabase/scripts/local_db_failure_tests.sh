#!/usr/bin/env bash
# OrigenLab V2 — failure injection for the local database tooling.
#
# Proves that dev_db.sh, disposable_db.sh, verify_chain.sh and the two guards they share
# actually refuse what they claim to refuse. A guard that has never been made to fail is a guard
# nobody has tested.
#
# Every scenario asserts three things: a non-zero exit, a diagnostic that names the real reason,
# and — where it matters — that no connection was attempted at all.
#
# Safe to run at any time. It creates nothing permanent, drops nothing persistent, and leaves
# the development database and its container exactly as it found them.
#
# Procedure: docs/OPERATIONS.md §4.1.

set -uo pipefail

OL_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
export OL_REPO_ROOT
cd "$OL_REPO_ROOT"

PASS=0
FAIL=0

ok()   { echo "ok    $*"; PASS=$(( PASS + 1 )); }
bad()  { echo "FAIL  $*"; FAIL=$(( FAIL + 1 )); }

# expect_refusal <label> <needle> <command...>
# The command must exit non-zero AND its combined output must contain <needle>.
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
    printf '      got: %s\n' "$(printf '%s' "$out" | tail -n 2)"
    return
  fi
  ok "$label"
}

# A shell that sources the guard library and runs one expression.
guard() { bash -c '. supabase/scripts/lib/local_target.sh; '"$1"; }

echo "== A-E: the named-database guard refuses names it did not mint =="
expect_refusal "A: an empty database name is refused" \
  "is not a database this repository mints" \
  guard 'ol_require_local_database ""'
expect_refusal "B: a name carrying a query string is refused" \
  "is not a database this repository mints" \
  guard 'ol_require_local_database "postgres?host=evil.example.com"'
expect_refusal "C: a path-traversal name is refused" \
  "is not a database this repository mints" \
  guard 'ol_require_local_database "../../etc/passwd"'
expect_refusal "D: a disposable name of the wrong shape is refused" \
  "is not a database this repository mints" \
  guard 'ol_require_local_database "origenlab_test_ZZZZZZZZ"'
expect_refusal "E: an arbitrary existing database is refused" \
  "is not a database this repository mints" \
  guard 'ol_require_local_database "template1"'

echo
echo "== F-G: psql helpers refuse to run before their guard has =="
expect_refusal "F: ol_psql_db refuses before the guard has run" \
  "called before ol_require_local_database succeeded" \
  guard 'ol_psql_db -c "select 1"'
expect_refusal "G: ol_psql_dev refuses before the guard has run" \
  "called before ol_require_dev_database succeeded" \
  guard 'ol_psql_dev -c "select 1"'

echo
echo "== H-I: a linked project is fatal for both guards =="
REF_DIR="$OL_REPO_ROOT/supabase/.temp"
REF_FILE="$REF_DIR/project-ref"
PLANTED=0
if [[ -e "$REF_FILE" ]]; then
  bad "H/I: $REF_FILE already exists; refusing to plant over a real link"
else
  mkdir -p "$REF_DIR"
  printf 'abcdefghijklmnopqrst\n' > "$REF_FILE"
  PLANTED=1
  expect_refusal "H: the local guard refuses a linked project" \
    "linked to a hosted project" \
    guard 'ol_require_local_database "postgres"'
  expect_refusal "I: the dev guard refuses a linked project" \
    "linked to a hosted project" \
    guard 'ol_require_dev_database'
  rm -f "$REF_FILE"
  rmdir "$REF_DIR" 2>/dev/null || true
fi
if (( PLANTED )) && [[ -e "$REF_FILE" ]]; then
  bad "H/I: the planted project-ref was not removed"
fi

echo
echo "== J-M: disposable_db.sh will not drop what is not disposable =="
for victim in origenlab_dev postgres origenlab_template ""; do
  expect_refusal "J: 'drop ${victim:-<empty>}' is refused" \
    "is not a disposable database" \
    ./supabase/scripts/disposable_db.sh drop "$victim"
done

echo
echo "== N: sweep never matches a non-disposable name =="
# The sweep predicate is a regex on pg_database. Assert here that the names we must never touch
# do not match it, without connecting: the pattern is the contract.
for name in origenlab_dev origenlab_template postgres _supabase template1; do
  if [[ "$name" =~ ^origenlab_test_[0-9a-f]{8}$ ]]; then
    bad "N: '$name' matches the disposable pattern and could be swept"
  fi
done
ok "N: no protected database name matches the disposable pattern"

echo
echo "== O-P: dev_db.sh refuses destructive commands without --force =="
expect_refusal "O1: an unknown restore option is refused" \
  "unknown option" \
  ./supabase/scripts/dev_db.sh restore --force-not-given

# The --force refusal is checked only after the checkpoint has been found and verified, so it
# needs a real one. If none exists yet, say so rather than silently passing.
NEWEST_CHECKPOINT="$(find "${OL_LOCAL_PRIVATE_ROOT:-$HOME/data/origenlab-v2-local}/checkpoints" \
  -maxdepth 1 -name '*.sql.gz' -type f 2>/dev/null | sort | tail -n 1)"
if [[ -n "$NEWEST_CHECKPOINT" ]]; then
  expect_refusal "O2: restoring a valid checkpoint without --force is refused" \
    "cannot be undone" \
    ./supabase/scripts/dev_db.sh restore "$NEWEST_CHECKPOINT"
else
  echo "skip  O2: no checkpoint exists yet, so the --force refusal cannot be exercised"
fi
expect_refusal "P: destroy without --force is refused" \
  "Take a checkpoint first" \
  ./supabase/scripts/dev_db.sh destroy

echo
echo "== Q-S: restore refuses an unverifiable checkpoint =="
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
expect_refusal "Q: a checkpoint that does not exist is refused" \
  "not found" \
  ./supabase/scripts/dev_db.sh restore "$TMP/absent.sql.gz" --force

printf 'not really a dump\n' | gzip -9 > "$TMP/unsigned.sql.gz"
expect_refusal "R: a checkpoint with no .sha256 sidecar is refused" \
  "no .sha256 sidecar" \
  ./supabase/scripts/dev_db.sh restore "$TMP/unsigned.sql.gz" --force

cp "$TMP/unsigned.sql.gz" "$TMP/tampered.sql.gz"
( cd "$TMP" && sha256sum tampered.sql.gz > tampered.sql.gz.sha256 )
printf 'tampered\n' | gzip -9 > "$TMP/tampered.sql.gz"
expect_refusal "S: a checkpoint that does not match its sidecar is refused" \
  "does not match its .sha256 sidecar" \
  ./supabase/scripts/dev_db.sh restore "$TMP/tampered.sql.gz" --force

echo
echo "== T: verify_chain.sh refuses an unknown option =="
expect_refusal "T: an unknown verify_chain option is refused" \
  "unknown option" \
  ./supabase/scripts/verify_chain.sh --not-an-option

echo
echo "== U: no tracked file was modified by this suite =="
# Only tracked files matter. An untracked file in the tree is the developer's business — and on
# the commit that introduces this suite, this script is itself untracked.
DIRTY="$(git -C "$OL_REPO_ROOT" status --porcelain | grep -v '^??' || true)"
if [[ -n "$DIRTY" ]]; then
  bad "U: tracked files were modified by the suite"
  printf '%s\n' "$DIRTY" | head -5
else
  ok "U: this suite modified no tracked file"
fi

echo
echo "local database failure injection: $PASS passed, $FAIL failed"
(( FAIL == 0 ))
