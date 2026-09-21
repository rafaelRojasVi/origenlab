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
#   2. it converges from the currently observed hosted shape -- `postgres` holding SET on
#      origenlab_owner -- and leaves `postgres` with no SET and no INHERIT on any OrigenLab role
#      while its creator-ADMIN rows survive untouched;
#   3. it is idempotent -- applied a second time in the same transaction it still succeeds and the
#      post-state is unchanged;
#   4. nothing else moves: no Supabase-managed role attribute, no membership that does not involve
#      an OrigenLab role, no schema, table, function or policy, and no row;
#   5. the role and membership catalogue is byte-identical before and after the rolled-back
#      transaction, so the rehearsal left nothing behind.
#
# The rehearsal no longer prepares a hosted-like shape before applying the file, and that change is
# the point of this revision. The local database, freshly reset from supabase/roles.sql, carries
# `postgres -> origenlab_owner` with SET -- byte for byte the shape the hosted origenlab-v2
# catalogue was measured to carry on 2026-09-20 (docs/STATUS.md §2.5). Applying the hosted file
# straight onto it is therefore a rehearsal of convergence from the real observed bad state, not a
# rehearsal against a shape arranged to make the file pass. The file's own
# `revoke set option for origenlab_owner from postgres` is what removes it.
#
# `postgres` may revoke only what it granted, so the creator-admin row PostgreSQL confers via
# `supabase_admin` survives -- which is exactly what docs/ARCHITECTURE.md §6.4 intends, and the
# assertions below prove it rather than assume it.
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

# One line of output per probe, so the whole "nothing else moved" question is answered by comparing
# two strings in bash rather than by trusting a narrative. Everything here is a read.
cat >"$WORK/digest.sql" <<'SQL'
select 'DIGEST ' || md5(string_agg(part, '|' order by part)) from (
  -- every role this bootstrap must not touch, with every attribute it could have changed
  select format('role:%s:%s:%s:%s:%s:%s:%s:%s:%s',
                r.rolname, r.rolsuper, r.rolinherit, r.rolcreaterole, r.rolcreatedb,
                r.rolcanlogin, r.rolreplication, r.rolbypassrls, r.rolconnlimit) as part
    from pg_roles r where r.rolname not like 'origenlab\_%'
  union all
  -- every membership that does not involve an OrigenLab role, with its options and grantor
  select format('member:%s:%s:%s:%s:%s:%s',
                m.rolname, mem.rolname, g.rolname,
                am.admin_option, am.inherit_option, am.set_option)
    from pg_auth_members am
    join pg_roles m on m.oid = am.roleid
    join pg_roles mem on mem.oid = am.member
    join pg_roles g on g.oid = am.grantor
   where m.rolname not like 'origenlab\_%' and mem.rolname not like 'origenlab\_%'
  union all
  select format('ns:%s:%s', n.nspname, n.nspowner::regrole) from pg_namespace n
  union all
  select format('rel:%s:%s:%s:%s', c.relnamespace::regnamespace, c.relname, c.relkind,
                c.relowner::regrole)
    from pg_class c
   where c.relnamespace::regnamespace::text not in ('pg_toast', 'pg_temp_1', 'pg_toast_temp_1')
  union all
  select format('proc:%s:%s:%s', p.pronamespace::regnamespace, p.proname, p.prosecdef)
    from pg_proc p
  union all
  select format('pol:%s:%s', pol.polrelid::regclass, pol.polname) from pg_policy pol
  union all
  select format('rls:%s:%s', c.oid::regclass, c.relrowsecurity)
    from pg_class c where c.relrowsecurity
  union all
  -- Every row of every business table, counted. The bootstrap contains no DML, so these counts
  -- must be identical across the three digests. Stated as an equality and never as an absolute:
  -- the seven schemas are NOT empty -- migration 20260905230814 seeds the outbound.send_control
  -- kill-switch singleton -- and they will hold far more once the CRM import lands. A rehearsal
  -- that asserted a constant here would be asserting the calendar, not the bootstrap.
  select format('rows:%s.%s:%s', n.nspname, c.relname,
                (xpath('/row/c/text()',
                       query_to_xml(format('select count(*) as c from %I.%I', n.nspname, c.relname),
                                    false, true, '')))[1]::text::bigint)
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
   where c.relkind = 'r'
     and n.nspname in ('crm','comms','outbound','evidence','catalog','procurement','platform')
) parts;
SQL

# The post-state the convergence must produce, asserted as a fail-closed DO block so a wrong result
# aborts the run under ON_ERROR_STOP rather than being read out of a log by eye.
cat >"$WORK/assert_post.sql" <<'SQL'
do $ol$
declare
  v_bad text;
  v_admin int;
begin
  -- 1. no platform identity may SET ROLE to, or inherit, any OrigenLab role.
  select string_agg(format('%s in %s (inherit=%s set=%s)', mem.rolname, m.rolname,
                           am.inherit_option, am.set_option), ', ')
    into v_bad
    from pg_auth_members am
    join pg_roles m on m.oid = am.roleid
    join pg_roles mem on mem.oid = am.member
   where m.rolname like 'origenlab\_%'
     and mem.rolname not like 'origenlab\_%'
     and (am.inherit_option or am.set_option);
  if v_bad is not null then
    raise exception 'REHEARSAL: convergence left a privilege-bearing platform membership: %', v_bad;
  end if;

  -- 2. the relationship the policy KEEPS: postgres still holds its creator ADMIN row on all four.
  select count(*) into v_admin
    from pg_auth_members am
    join pg_roles m on m.oid = am.roleid
    join pg_roles mem on mem.oid = am.member
   where m.rolname like 'origenlab\_%' and mem.rolname = 'postgres' and am.admin_option;
  if v_admin <> 4 then
    raise exception 'REHEARSAL: postgres should hold ADMIN on all four OrigenLab roles, holds %',
      v_admin;
  end if;

  -- 3. the one membership the hosted model wants, unchanged and still SET-only.
  if not exists (
    select 1 from pg_auth_members am
      join pg_roles m on m.oid = am.roleid
      join pg_roles mem on mem.oid = am.member
     where m.rolname = 'origenlab_owner' and mem.rolname = 'origenlab_migrator'
       and am.set_option and not am.inherit_option
  ) then
    raise exception 'REHEARSAL: the migrator lost its SET-only membership in the owner';
  end if;

  -- Rows are deliberately NOT asserted here. "No row moved" is a before/after claim, and it is
  -- proven as one: the digest taken three times around the two applications counts every row of
  -- every business table. An absolute check belongs to no era -- the seven schemas already hold
  -- the outbound.send_control singleton, and the CRM import will add many more.
end
$ol$;
SQL

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

# The precondition that makes this a rehearsal of the real problem: the local database must be in
# the shape the hosted catalogue was measured in -- `postgres` holding SET on origenlab_owner. If
# supabase/roles.sql ever stops creating it, this rehearsal silently stops proving anything.
OBSERVED_SHAPE_QUERY="select exists (
  select 1
    from pg_auth_members am
    join pg_roles m on m.oid = am.roleid
    join pg_roles mem on mem.oid = am.member
   where m.rolname = 'origenlab_owner' and mem.rolname = 'postgres' and am.set_option);"

observed_shape="$(ol_psql -q -A -t -c "$OBSERVED_SHAPE_QUERY" 2>&1 | tr -d '[:space:]')"
check "the local database is in the observed hosted shape before the rehearsal" \
  "$([[ "$observed_shape" == "t" ]] && echo 0 || echo 1)" \
  "postgres does not hold SET on origenlab_owner locally; the rehearsal would prove nothing"

# One transaction, applied twice, always rolled back, with NO preparation of the catalogue. `set
# role` is not used and not needed: the CLI's postgres login is the same identity that would apply
# this file on a hosted project.
rc=0
{
  echo "begin;"
  cat "$WORK/digest.sql"
  cat "$BOOTSTRAP"
  echo ";"
  cat "$WORK/assert_post.sql"
  cat "$WORK/digest.sql"
  cat "$BOOTSTRAP"
  echo ";"
  cat "$WORK/assert_post.sql"
  cat "$WORK/digest.sql"
  echo "rollback;"
} | ol_psql -q -A -t -v ON_ERROR_STOP=1 -f - >"$WORK/apply" 2>&1 || rc=$?

check "the bootstrap converges from the observed hosted shape, is idempotent, then rolls back" \
  "$([[ $rc -eq 0 ]] && echo 0 || echo 1)" "$(tail -3 "$WORK/apply" | tr '\n' ' ')"

check "no assertion in the file raised" \
  "$(grep -qiE '^(psql:)?.*ERROR:' "$WORK/apply" && echo 1 || echo 0)" \
  "$(grep -iE 'ERROR:' "$WORK/apply" | head -2 | tr '\n' ' ')"

# The post-state assertions ran twice -- once after each application -- and both had to pass for
# the run to reach `rollback`. That is the idempotency claim stated as a proof rather than as an
# exit code: `postgres` holds no SET and no INHERIT, still holds ADMIN on all four, the migrator
# keeps its SET-only membership, and every per-table row count across the seven schemas is
# unchanged. Stated as an equality, never as an absolute: outbound.send_control legitimately
# holds its singleton kill-switch row, so "zero rows" would be a false invariant.

mapfile -t DIGESTS < <(grep '^DIGEST ' "$WORK/apply" || true)

check "three digests were taken: before, after one application, after two" \
  "$([[ ${#DIGESTS[@]} -eq 3 ]] && echo 0 || echo 1)" "got ${#DIGESTS[@]}"

digests_equal=1
if [[ ${#DIGESTS[@]} -eq 3 && "${DIGESTS[0]}" == "${DIGESTS[1]}" && "${DIGESTS[1]}" == "${DIGESTS[2]}" ]]; then
  digests_equal=0
fi
check "no Supabase-managed role, membership, schema, table, function or policy moved" \
  "$digests_equal" "${DIGESTS[*]:-no digest was produced}"

# The negative half: the file's own platform-boundary assertion must actually fire. An assertion
# that cannot fail is not evidence. A SET-bearing membership is granted to a platform role inside
# the same rolled-back transaction, and the bootstrap must refuse it.
#
# The role chosen matters. The convergence this file now performs is scoped to ONE pair --
# `postgres` on origenlab_owner -- so a SET-bearing membership on a *runtime* role is outside it
# and must still refuse. That is what keeps the exception narrow instead of general.
rc=0
{
  echo "begin;"
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

# The local/hosted divergence, restated for what the files now do. Before the convergence existed,
# the hosted file *refused* the local database, and that refusal was the proof the two are not
# interchangeable. It now converges instead -- so the proof has to change with it, and the thing to
# prove is stronger: applied to the local database the hosted file REMOVES the membership
# supabase/roles.sql exists to create, which is precisely why it must never be applied locally.
#
# Measured inside the transaction, before the rollback puts it back.
rc=0
{
  echo "begin;"
  echo "select 'LOCAL_GRANT_BEFORE ' || exists (select 1 from pg_auth_members am"
  echo "  join pg_roles m on m.oid = am.roleid join pg_roles mem on mem.oid = am.member"
  echo " where m.rolname = 'origenlab_owner' and mem.rolname = 'postgres' and am.set_option);"
  cat "$BOOTSTRAP"
  echo ";"
  echo "select 'LOCAL_GRANT_AFTER ' || exists (select 1 from pg_auth_members am"
  echo "  join pg_roles m on m.oid = am.roleid join pg_roles mem on mem.oid = am.member"
  echo " where m.rolname = 'origenlab_owner' and mem.rolname = 'postgres' and am.set_option);"
  echo "rollback;"
} | ol_psql -q -A -t -v ON_ERROR_STOP=1 -f - >"$WORK/local_shape" 2>&1 || rc=$?

check "applied to the local database as-is, the hosted file now converges instead of refusing" \
  "$([[ $rc -eq 0 ]] && echo 0 || echo 1)" "$(tail -2 "$WORK/local_shape" | tr '\n' ' ')"
check "it removes the local postgres SET membership, so the two files are not interchangeable" \
  "$(grep -qx 'LOCAL_GRANT_BEFORE true' "$WORK/local_shape" &&
     grep -qx 'LOCAL_GRANT_AFTER false' "$WORK/local_shape" && echo 0 || echo 1)" \
  "$(grep '^LOCAL_GRANT' "$WORK/local_shape" | tr '\n' ' ')"

ol_psql -q -A -t -c "$CATALOGUE_QUERY" >"$WORK/after_local_shape" 2>&1
check "the rollback restored the local membership the hosted file removed" \
  "$(cmp -s "$WORK/before" "$WORK/after_local_shape" && echo 0 || echo 1)" \
  "$(diff "$WORK/before" "$WORK/after_local_shape" | head -4 | tr '\n' ' ')"

echo
echo "hosted role bootstrap rehearsal: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
