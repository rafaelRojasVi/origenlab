#!/usr/bin/env bash
# OrigenLab V2 — execution rehearsal for the hosted role bootstrap.
#
# The static analyser (supabase/audit/olaudit/bootstrap.py) proves what supabase/hosted_roles.sql
# may touch. It cannot prove the file *runs*, and the difference is not academic: the file's
# fail-closed platform-boundary assertion has to be right about how PostgreSQL 16+ records a role
# creator's implicit ADMIN OPTION, and a wrong assertion there is a file that refuses itself the
# first time an operator applies it to a hosted project. That failure would be discovered on the
# hosted project, which is the worst possible place to discover it.
#
# So the file is rehearsed here, against the DISPOSABLE LOCAL DATABASE ONLY, inside one explicit
# transaction that is ALWAYS ROLLED BACK. The rehearsal proves three things:
#
#   1. the file executes to completion with no error, so its DO blocks, its convergence statements
#      and its fail-closed assertions are all valid against a real PostgreSQL 17 catalogue;
#   2. it is idempotent -- applied a second time in the same transaction it still succeeds;
#   3. the role and membership catalogue is byte-identical before and after the rolled-back
#      transaction, so the rehearsal left nothing behind.
#
# The transaction first puts the session into HOSTED-LIKE SHAPE by revoking the one membership that
# exists locally and must not exist hosted: supabase/roles.sql grants the CLI's `postgres` login a
# SET-only membership in origenlab_owner so local migrations can run, and supabase/hosted_roles.sql
# asserts that no identity outside the OrigenLab set may inherit or assume an OrigenLab role. Both
# are correct for their own environment, and rehearsing the hosted file without that revoke would
# only prove that the local exception exists. The revoke removes just that row -- `postgres` may
# revoke only what it granted, so the creator-admin row that PostgreSQL confers via `supabase_admin`
# survives, which is exactly the shape a fresh hosted project has.
#
# The target is resolved exclusively by supabase/scripts/lib/local_target.sh, exactly as every
# other script in docs/OPERATIONS.md §4.1 does, so this cannot be pointed at a hosted project: the
# guard refuses any non-loopback host before a connection is opened. There is no hosted mode here
# and no way to add one from the command line -- this script takes no arguments at all.
#
# Preconditions: the local stack is running and freshly reset. Procedure: docs/OPERATIONS.md §4.3.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

BOOTSTRAP="supabase/hosted_roles.sql"

# shellcheck source=lib/local_target.sh
source "$ROOT/supabase/scripts/lib/local_target.sh"
ol_require_local_target "$ROOT" || {
  echo "FAIL: local target validation failed; nothing was executed" >&2
  exit 2
}

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf 'ok    %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf 'not ok %s  [%s]\n' "$1" "${2:-}"; }
check() { if [[ "$2" == 0 ]]; then ok "$1"; else bad "$1" "${3:-}"; fi }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# The catalogue fact this rehearsal must not disturb: every OrigenLab role attribute and every
# membership row touching one, with its options and its grantor.
CATALOGUE_QUERY="
select r.rolname, r.rolcanlogin, r.rolinherit, r.rolsuper, r.rolbypassrls, r.rolreplication,
       r.rolcreatedb, r.rolcreaterole
  from pg_roles r where r.rolname like 'origenlab\\_%' order by 1;
select m.rolname, mem.rolname, g.rolname, am.admin_option, am.inherit_option, am.set_option
  from pg_auth_members am
  join pg_roles m on m.oid = am.roleid
  join pg_roles mem on mem.oid = am.member
  join pg_roles g on g.oid = am.grantor
 where m.rolname like 'origenlab\\_%' or mem.rolname like 'origenlab\\_%'
 order by 1, 2, 3;"

echo "== hosted role bootstrap: execution rehearsal (local, rolled back) =="
echo

# Guard: the file the analyser approved is the file that gets executed. Running an unanalysed file
# here would make the rehearsal the one place the closed-role-set proof does not apply.
if python3 -c "
import sys, pathlib
sys.path.insert(0, 'supabase/audit')
from olaudit import bootstrap
bootstrap.load(pathlib.Path('.'))" >"$WORK/analyse" 2>&1; then
  ok "the file passes the static analyser before it is executed"
else
  bad "the file passes the static analyser before it is executed" "$(cat "$WORK/analyse")"
  echo; echo "hosted role bootstrap rehearsal: $PASS passed, $FAIL failed"; exit 1
fi

ol_psql -q -A -t -c "$CATALOGUE_QUERY" >"$WORK/before" 2>&1

# One transaction, applied twice, always rolled back. `set role` is not used and not needed: the
# CLI's postgres login is the same identity that would apply this file on a hosted project.
rc=0
{
  echo "begin;"
  echo "revoke origenlab_owner from postgres;"   # hosted-like shape; see the header
  cat "$BOOTSTRAP"
  echo ";"
  cat "$BOOTSTRAP"
  echo ";"
  echo "rollback;"
} | ol_psql -q -v ON_ERROR_STOP=1 -f - >"$WORK/apply" 2>&1 || rc=$?

check "the bootstrap executes to completion and is idempotent, then rolls back" \
  "$([[ $rc -eq 0 ]] && echo 0 || echo 1)" "$(tail -3 "$WORK/apply" | tr '\n' ' ')"

check "no assertion in the file raised" \
  "$(grep -qiE '^(psql:)?.*ERROR:' "$WORK/apply" && echo 1 || echo 0)" \
  "$(grep -iE 'ERROR:' "$WORK/apply" | head -2 | tr '\n' ' ')"

ol_psql -q -A -t -c "$CATALOGUE_QUERY" >"$WORK/after" 2>&1
check "the role and membership catalogue is unchanged by the rehearsal" \
  "$(cmp -s "$WORK/before" "$WORK/after" && echo 0 || echo 1)" \
  "$(diff "$WORK/before" "$WORK/after" | head -4 | tr '\n' ' ')"

# The negative half: the file's own platform-boundary assertion must actually fire. An assertion
# that cannot fail is not evidence. A SET-bearing membership is granted to a platform role inside
# the same rolled-back transaction, and the bootstrap must refuse it.
rc=0
{
  echo "begin;"
  echo "revoke origenlab_owner from postgres;"
  echo "grant origenlab_api to postgres with inherit false, set true;"
  cat "$BOOTSTRAP"
  echo ";"
  echo "rollback;"
} | ol_psql -q -v ON_ERROR_STOP=1 -f - >"$WORK/negative" 2>&1 || rc=$?

check "a platform identity given SET ROLE on a runtime role makes the bootstrap refuse" \
  "$([[ $rc -ne 0 ]] && echo 0 || echo 1)" "the assertion did not fire"
check "the refusal names the boundary it is defending" \
  "$(grep -qi 'may inherit or assume an OrigenLab role' "$WORK/negative" && echo 0 || echo 1)" \
  "$(grep -i 'ERROR' "$WORK/negative" | head -1)"

ol_psql -q -A -t -c "$CATALOGUE_QUERY" >"$WORK/after_negative" 2>&1
check "the negative rehearsal also left the catalogue unchanged" \
  "$(cmp -s "$WORK/before" "$WORK/after_negative" && echo 0 || echo 1)" \
  "$(diff "$WORK/before" "$WORK/after_negative" | head -4 | tr '\n' ' ')"

# And the converse, which is what makes the local/hosted divergence real rather than stylistic:
# applied WITHOUT the hosted-like revoke, the hosted file refuses the local database on the very
# grant supabase/roles.sql adds. The two files are not interchangeable, and this proves it.
rc=0
{
  echo "begin;"
  cat "$BOOTSTRAP"
  echo ";"
  echo "rollback;"
} | ol_psql -q -v ON_ERROR_STOP=1 -f - >"$WORK/local_shape" 2>&1 || rc=$?

check "applied to the local database as-is, the hosted file refuses the local postgres grant" \
  "$([[ $rc -ne 0 ]] && echo 0 || echo 1)" "the hosted file accepted the local exception"
check "the two bootstrap files are therefore not interchangeable" \
  "$(grep -qi 'postgres in origenlab_owner' "$WORK/local_shape" && echo 0 || echo 1)" \
  "$(grep -i 'ERROR' "$WORK/local_shape" | head -1)"

echo
echo "hosted role bootstrap rehearsal: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
