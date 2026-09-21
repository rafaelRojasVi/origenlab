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

# ==================================================================================================
# The atomic application contract -- docs/OPERATIONS.md §4.3, "one transaction, or nothing".
#
# Everything above rehearses the file inside an explicit `begin; ... rollback;` this script writes.
# That proves the SQL runs. It proves nothing about how an operator is required to APPLY it, and
# the two are different claims: an operator who runs the same bytes without `--single-transaction`
# gets a half-converged project on the first failure, and no assertion in the file can stop them.
#
# So the contract itself is the thing under test here. The documented invocation is EXTRACTED from
# docs/OPERATIONS.md by its `ol:hosted-apply-contract` anchor and executed -- not paraphrased. A
# paraphrase would prove only that this script agrees with itself; an extraction fails the moment
# the document and the rehearsal drift apart. Exactly three localisations are applied to it, each
# asserted to have applied exactly once, and every element the contract actually turns on --
# `env -i`, `-X`, `--no-psqlrc`, command-line `ON_ERROR_STOP=1`, `--single-transaction` -- is
# executed verbatim:
#
#   1. PGSSLMODE=verify-full -> disable, because the local container speaks no TLS at all;
#   2. the PGSSLROOTCERT line is dropped, for the same reason;
#   3. `-f supabase/hosted_roles.sql` -> `-f "$OL_APPLY_FILE"`, so a failure can be injected around
#      the committed bytes. The committed bytes are still what is applied -- each injected file is
#      the file itself with one statement placed before or after it.
#
# The environment this runs in is deliberately HOSTILE, so that neutralisation is observed rather
# than assumed. Before each run: $HOME holds a `.psqlrc` that would turn ON_ERROR_STOP off and
# create a role, and PGHOST, PGSERVICE, PGSERVICEFILE, PGOPTIONS, PGSSLMODE, PGPASSFILE and PSQLRC
# are all exported pointing somewhere wrong. The contract's `env -i` and `-X` are the only things
# standing between that environment and the connection, and the run is required to succeed in
# reaching the local database anyway.
#
# NOTHING HERE IS ALLOWED TO COMMIT. Every injected file ends in, or triggers, a failure, so every
# contract run rolls back by the contract's own mechanism -- there is no `rollback;` written by this
# script in this section, because a written rollback would mask the absence of the flag being
# tested. The one run that is permitted to commit is the negative control for the psqlrc detector,
# which is cleaned up explicitly and then proven gone by the catalogue comparison at the end.
# ==================================================================================================

CONTRACT_DOC="docs/OPERATIONS.md"
CONTRACT_ANCHOR="ol:hosted-apply-contract"

PROBE_ROLES=(ol_rehearsal_probe ol_contract_probe ol_psqlrc_probe)

ol_drop_probe_roles() {
  local r
  for r in "${PROBE_ROLES[@]}"; do
    ol_psql -q -c "drop role if exists $r;" >/dev/null 2>&1 || true
  done
}
# Unconditional: a probe role that survived an unexpected commit must not outlive this script.
trap 'ol_drop_probe_roles; rm -rf "$WORK"' EXIT

ol_probe_absent() { # $1 = role name; echoes 0 when the role does not exist
  local present
  present="$(ol_psql -q -A -t -c "select exists (select 1 from pg_roles where rolname = '$1');" 2>&1 |
             tr -d '[:space:]')"
  [[ "$present" == "f" ]] && echo 0 || echo 1
}

ol_postgres_option() { # $1 = OrigenLab role, $2 = set_option|inherit_option; echoes true/none
  ol_psql -q -A -t -c "
    select coalesce(max($2::text), 'none')
      from pg_auth_members am
      join pg_roles m on m.oid = am.roleid
      join pg_roles mem on mem.oid = am.member
     where m.rolname = '$1' and mem.rolname = 'postgres' and am.$2;" 2>&1 | tr -d '[:space:]'
}

python3 - "$CONTRACT_DOC" "$CONTRACT_ANCHOR" "$WORK" >"$WORK/contract.report" 2>&1 <<'PY' || true
import pathlib, sys

doc, anchor, work = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3])
lines = pathlib.Path(doc).read_text(encoding="utf-8").splitlines()

start = next((i for i, l in enumerate(lines) if anchor in l), None)
if start is None:
    print("ERROR no anchor")
    raise SystemExit(0)
open_fence = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("```")), None)
if open_fence is None or lines[open_fence].strip() != "```bash":
    print("ERROR no bash fence after the anchor")
    raise SystemExit(0)
close_fence = next((i for i in range(open_fence + 1, len(lines)) if lines[i].startswith("```")), None)
if close_fence is None:
    print("ERROR unterminated fence")
    raise SystemExit(0)

block = "\n".join(lines[open_fence + 1:close_fence]) + "\n"
(work / "contract.sh").write_text(block, encoding="utf-8")

# The psql command line, logically: the backslash-continued line that invokes psql.
joined = block.replace("\\\n", " ")
# The continuation-joined text is one logical line; the argument vector starts at `psql `.
cut = joined.find("psql ")
psql_line = joined[cut:] if cut != -1 else ""

required_in_argv = {
    "--single-transaction": "--single-transaction",
    "ON_ERROR_STOP-on-the-command-line": "-v ON_ERROR_STOP=1",
    "-X": " -X ",
    "--no-psqlrc": "--no-psqlrc",
    "the-committed-file": "-f supabase/hosted_roles.sql",
}
required_in_block = {
    "clean-child-environment": "env -i",
    "verify-full": "PGSSLMODE=verify-full",
    "validated-CA": "PGSSLROOTCERT=",
    "password-via-environment": "PGPASSWORD=",
}
forbidden = {
    "connection-string-in-argv": "postgres://",
    "connection-string-in-argv-ql": "postgresql://",
    "password-prompt-flag": "--password",
    "inline-sql": " -c ",
    "target-flag": " -d ",
}

for name, token in required_in_argv.items():
    print("ARGV", "OK" if token in f" {psql_line} " else "MISSING", name)
for name, token in required_in_block.items():
    print("BLOCK", "OK" if token in block else "MISSING", name)
for name, token in forbidden.items():
    print("FORBIDDEN", "PRESENT" if token in block else "OK", name)

# The three localisations, each required to apply exactly once.
local_block, n = block, []
local_block, c = local_block.replace("PGSSLMODE=verify-full", "PGSSLMODE=disable"), local_block.count("PGSSLMODE=verify-full"); n.append(("sslmode", c))
stripped = [l for l in local_block.splitlines() if "PGSSLROOTCERT=" not in l]
n.append(("sslrootcert", len(local_block.splitlines()) - len(stripped)))
local_block = "\n".join(stripped) + "\n"
c = local_block.count("-f supabase/hosted_roles.sql")
local_block = local_block.replace("-f supabase/hosted_roles.sql", '-f "$OL_APPLY_FILE"')
n.append(("apply-file", c))
for name, count in n:
    print("LOCALISE", "OK" if count == 1 else f"COUNT={count}", name)
(work / "contract.local.sh").write_text(local_block, encoding="utf-8")

# The localisation must not have touched anything else the contract turns on.
untouched = all(t in local_block for t in
                ("env -i", "-X", "--no-psqlrc", "-v ON_ERROR_STOP=1", "--single-transaction"))
print("LOCALISE", "OK" if untouched else "LOST", "flags-survived-localisation")
PY

check "the apply contract is extractable from ${CONTRACT_DOC} by its anchor" \
  "$([[ -s "$WORK/contract.sh" && -s "$WORK/contract.local.sh" ]] && echo 0 || echo 1)" \
  "$(head -3 "$WORK/contract.report" | tr '\n' ' ')"

check "the documented invocation carries --single-transaction and command-line ON_ERROR_STOP=1" \
  "$(grep -q '^ARGV OK --single-transaction$' "$WORK/contract.report" &&
     grep -q '^ARGV OK ON_ERROR_STOP-on-the-command-line$' "$WORK/contract.report" &&
     echo 0 || echo 1)" \
  "$(grep -E '^ARGV (MISSING|OK) (--single-transaction|ON_ERROR_STOP)' "$WORK/contract.report" | tr '\n' ' ')"

check "the documented invocation carries -X, --no-psqlrc and the committed file itself" \
  "$(grep -qc '^ARGV MISSING' "$WORK/contract.report" && echo 1 || echo 0)" \
  "$(grep '^ARGV MISSING' "$WORK/contract.report" | tr '\n' ' ')"

check "the documented invocation builds its own environment, pins verify-full and names a CA" \
  "$(grep -qc '^BLOCK MISSING' "$WORK/contract.report" && echo 1 || echo 0)" \
  "$(grep '^BLOCK MISSING' "$WORK/contract.report" | tr '\n' ' ')"

check "the documented invocation puts no connection string, password or inline SQL in argv" \
  "$(grep -qc '^FORBIDDEN PRESENT' "$WORK/contract.report" && echo 1 || echo 0)" \
  "$(grep '^FORBIDDEN PRESENT' "$WORK/contract.report" | tr '\n' ' ')"

check "exactly the three documented localisations were applied, and no flag was lost" \
  "$(grep -qc '^LOCALISE \(COUNT\|LOST\)' "$WORK/contract.report" && echo 1 || echo 0)" \
  "$(grep -E '^LOCALISE (COUNT|LOST)' "$WORK/contract.report" | tr '\n' ' ')"

# A fourth localisation, applied only when this machine needs it and reported when it is: the
# contract's PATH is the minimal one an operator has, and a runner that keeps psql somewhere else
# would otherwise fail this section for a reason that has nothing to do with atomicity.
CONTRACT_PATH_NOTE="documented PATH used as written"
if ! env -i PATH=/usr/bin:/bin sh -c 'command -v psql' >/dev/null 2>&1; then
  psql_dir="$(dirname "$(command -v psql)")"
  sed -i "s#^  PATH=/usr/bin:/bin \\\\\$#  PATH=$psql_dir:/usr/bin:/bin \\\\#" "$WORK/contract.local.sh"
  CONTRACT_PATH_NOTE="PATH localised to $psql_dir because the documented PATH does not resolve psql here"
fi
echo "contract: $CONTRACT_PATH_NOTE"

# ---- the hostile environment the contract has to neutralise ---------------------------------------
HOSTILE_HOME="$WORK/hostile_home"
mkdir -p "$HOSTILE_HOME"
cat >"$HOSTILE_HOME/.psqlrc" <<'PSQLRC'
\set ON_ERROR_STOP off
create role ol_psqlrc_probe nologin;
PSQLRC
cat >"$HOSTILE_HOME/pg_service.conf" <<'SVC'
[ol-bogus]
host=192.0.2.1
port=1
dbname=nonexistent
SVC

# The local target's own components, bound to the names the documented block reads. The password is
# taken from the validated URL and never printed.
CONTRACT_PW="$(python3 -c "
import os, urllib.parse as u
print(u.unquote(u.urlsplit(os.environ['OL_DB_URL']).password or ''))")"
export OL_HOSTED_HOST="$OL_DB_HOST" OL_HOSTED_PORT="$OL_DB_PORT" OL_HOSTED_USER="$OL_DB_USER" \
       OL_HOSTED_DATABASE="$OL_DB_NAME" OL_HOSTED_PASSWORD="$CONTRACT_PW"

run_contract() { # $1 = SQL file to apply, $2 = stdout/stderr capture, $3 = "keep-x" to drop -X
  local script="$WORK/contract.local.sh"
  if [[ "${3:-}" == "no-x" ]]; then
    script="$WORK/contract.nox.sh"
    sed -e 's/ -X --no-psqlrc//' "$WORK/contract.local.sh" >"$script"
  fi
  local rc=0
  env OL_APPLY_FILE="$1" \
      HOME="$HOSTILE_HOME" \
      PSQLRC="$HOSTILE_HOME/.psqlrc" \
      PGHOST=192.0.2.1 PGPORT=1 PGUSER=nobody PGDATABASE=nonexistent \
      PGSERVICE=ol-bogus PGSERVICEFILE="$HOSTILE_HOME/pg_service.conf" \
      PGOPTIONS='-c default_transaction_read_only=on' PGSSLMODE=require PGPASSFILE=/dev/null \
      OL_HOSTED_HOST="$OL_HOSTED_HOST" OL_HOSTED_PORT="$OL_HOSTED_PORT" \
      OL_HOSTED_USER="$OL_HOSTED_USER" OL_HOSTED_DATABASE="$OL_HOSTED_DATABASE" \
      OL_HOSTED_PASSWORD="$OL_HOSTED_PASSWORD" PATH="$PATH" \
      bash "$script" >"$2" 2>&1 || rc=$?
  echo "$rc"
}

# ---- C0: the contract reaches the local database through the hostile environment, and rolls back --
cat >"$WORK/c_probe.sql" <<'SQL'
select 'CONTRACT_DB ' || current_database();
select 'CONTRACT_READONLY ' || current_setting('transaction_read_only');
create role ol_contract_probe nologin;
select 'CONTRACT_DDL ' || exists (select 1 from pg_roles where rolname = 'ol_contract_probe');
do $ol$ begin raise exception 'REHEARSAL-INJECT: the contract probe always rolls back'; end $ol$;
SQL

rc="$(run_contract "$WORK/c_probe.sql" "$WORK/c_probe.out")"
check "the contract's clean environment beats an inherited PGHOST/PGSERVICE/PGOPTIONS" \
  "$(grep -q 'CONTRACT_DB *| *postgres\|CONTRACT_DB postgres' "$WORK/c_probe.out" && echo 0 || echo 1)" \
  "$(tail -3 "$WORK/c_probe.out" | tr '\n' ' ')"
check "an inherited PGOPTIONS did not make the session read-only" \
  "$(grep -q 'CONTRACT_READONLY *| *off\|CONTRACT_READONLY off' "$WORK/c_probe.out" && echo 0 || echo 1)" \
  "$(grep -i 'CONTRACT_READONLY' "$WORK/c_probe.out" | tr '\n' ' ')"
check "the probe's role DDL executed inside the contract transaction" \
  "$(grep -qi 'CONTRACT_DDL *| *t\|CONTRACT_DDL t' "$WORK/c_probe.out" && echo 0 || echo 1)" \
  "$(grep -i 'CONTRACT_DDL' "$WORK/c_probe.out" | tr '\n' ' ')"
check "a failure at the end of the file makes the contract run exit non-zero" \
  "$([[ $rc -ne 0 ]] && echo 0 || echo 1)" "rc=$rc"
check "the role created before that failure did not survive the run" \
  "$(ol_probe_absent ol_contract_probe)" "ol_contract_probe survived a rolled-back contract run"

# ---- C1: a failure AFTER an earlier successful role statement rolls that statement back -----------
# The injected statement is a plain `create role`, placed BEFORE the committed bytes, so it has
# certainly succeeded by the time the deliberate failure fires at the end. Its survival would be
# the exact partial application the contract exists to prevent.
{
  echo "create role ol_rehearsal_probe nologin;"
  cat "$BOOTSTRAP"
  echo ";"
  echo "do \$ol\$ begin raise exception 'REHEARSAL-INJECT: deliberate failure after a successful role statement'; end \$ol\$;"
} >"$WORK/inject_after.sql"

rc="$(run_contract "$WORK/inject_after.sql" "$WORK/inject_after.out")"
check "a failure after an earlier successful role statement aborts the run" \
  "$([[ $rc -ne 0 ]] && echo 0 || echo 1)" "rc=$rc"
check "the run reached the injected failure, so the bootstrap itself had already applied" \
  "$(grep -q 'REHEARSAL-INJECT: deliberate failure' "$WORK/inject_after.out" && echo 0 || echo 1)" \
  "$(tail -3 "$WORK/inject_after.out" | tr '\n' ' ')"
check "the earlier successful role statement was rolled back with it" \
  "$(ol_probe_absent ol_rehearsal_probe)" \
  "ol_rehearsal_probe survived; the application was NOT atomic"
check "no partial convergence survived: postgres still holds SET on origenlab_owner" \
  "$([[ "$(ol_postgres_option origenlab_owner set_option)" == "true" ]] && echo 0 || echo 1)" \
  "the file's own revoke was committed even though the run failed"

ol_psql -q -A -t -c "$CATALOGUE_QUERY" >"$WORK/after_inject_after" 2>&1
check "the aborted contract run left the role catalogue byte-identical" \
  "$(cmp -s "$WORK/before" "$WORK/after_inject_after" && echo 0 || echo 1)" \
  "$(diff "$WORK/before" "$WORK/after_inject_after" | head -4 | tr '\n' ' ')"

# ---- C2: the file's own assertions execute inside the same transaction ---------------------------
# A SET-bearing membership is granted to a platform identity on a RUNTIME role first. Every
# statement in the bootstrap then applies successfully -- the roles are created, the attributes and
# the owner membership are converged, and the two option revocations on origenlab_owner land -- and
# only the platform-boundary assertion at the very END of the file fails. If that assertion ran
# outside the transaction, or after it, the convergence would already be committed.
{
  echo "grant origenlab_api to postgres with inherit false, set true;"
  cat "$BOOTSTRAP"
  echo ";"
} >"$WORK/inject_assert.sql"

rc="$(run_contract "$WORK/inject_assert.sql" "$WORK/inject_assert.out")"
check "the file's final assertion fires under the contract invocation" \
  "$([[ $rc -ne 0 ]] && echo 0 || echo 1)" "rc=$rc"
check "the refusal is the platform-boundary assertion, not a syntax error" \
  "$(grep -qi 'may inherit or assume an OrigenLab role' "$WORK/inject_assert.out" && echo 0 || echo 1)" \
  "$(grep -i 'ERROR' "$WORK/inject_assert.out" | head -1)"
check "the assertion ran in the same transaction: the grant it caught was rolled back" \
  "$([[ "$(ol_postgres_option origenlab_api set_option)" == "none" ]] && echo 0 || echo 1)" \
  "postgres kept SET on origenlab_api, so the assertion did not undo what preceded it"
check "and the convergence the file had already performed was rolled back with it" \
  "$([[ "$(ol_postgres_option origenlab_owner set_option)" == "true" ]] && echo 0 || echo 1)" \
  "the option revocation survived an assertion failure; the application was NOT atomic"

ol_psql -q -A -t -c "$CATALOGUE_QUERY" >"$WORK/after_inject_assert" 2>&1
check "an assertion failure leaves no partial role or membership convergence behind" \
  "$(cmp -s "$WORK/before" "$WORK/after_inject_assert" && echo 0 || echo 1)" \
  "$(diff "$WORK/before" "$WORK/after_inject_assert" | head -4 | tr '\n' ' ')"

# ---- C3: psqlrc is neutralised, and the detector that says so is not vacuous ----------------------
check "the hostile ~/.psqlrc never ran under the documented invocation" \
  "$(ol_probe_absent ol_psqlrc_probe)" \
  "ol_psqlrc_probe exists; -X did not suppress the startup file"

# The negative control. Without -X the same startup file DOES run, and it runs BEFORE
# --single-transaction opens its transaction, so its role is committed even though the run fails.
# That is the whole reason -X is in the contract, and proving it here is what stops the check above
# from passing for the wrong reason.
rc="$(run_contract "$WORK/c_probe.sql" "$WORK/c_probe_nox.out" no-x)"
nox_created="$(ol_probe_absent ol_psqlrc_probe)"
ol_psql -q -c "drop role if exists ol_psqlrc_probe;" >/dev/null 2>&1 || true
check "without -X that same startup file does run and commits outside the transaction" \
  "$([[ "$nox_created" == "1" ]] && echo 0 || echo 1)" \
  "the psqlrc detector is vacuous: the startup file did nothing even without -X"

check "every probe role this section created is gone" \
  "$([[ "$(ol_probe_absent ol_rehearsal_probe)" == 0 && "$(ol_probe_absent ol_contract_probe)" == 0 &&
        "$(ol_probe_absent ol_psqlrc_probe)" == 0 ]] && echo 0 || echo 1)" \
  "a probe role outlived the section; the next gate would see it"

ol_psql -q -A -t -c "$CATALOGUE_QUERY" >"$WORK/after_contract" 2>&1
check "the whole contract section left the role catalogue byte-identical" \
  "$(cmp -s "$WORK/before" "$WORK/after_contract" && echo 0 || echo 1)" \
  "$(diff "$WORK/before" "$WORK/after_contract" | head -4 | tr '\n' ' ')"

echo
echo "hosted role bootstrap rehearsal: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
