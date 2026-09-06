#!/usr/bin/env bash
# OrigenLab V2 — Slice 0 deterministic reset/replay evidence.
#
# Resets the local database from roles.sql + migrations twice; after each reset it dumps the seven
# application schemas (DDL, constraints, indexes, grants, policies, comments), snapshots the
# role/membership/default-privilege/policy catalogue (no password is read or written), and lists the
# applied migrations. Every CLI exit status is enforced, every artefact is asserted complete before
# any comparison is made, and the single PASS conclusion is printed only when every assertion has
# succeeded. Output directory: $1 or a fresh temp dir.
#
# Design: docs/ARCHITECTURE.md §6. Procedure: docs/OPERATIONS.md §4.1.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
# shellcheck source=lib/local_target.sh
source "$ROOT/supabase/scripts/lib/local_target.sh"

OUT="${1:-$(mktemp -d)}"
mkdir -p "$OUT"
SCHEMAS="crm,comms,outbound,evidence,catalog,procurement,platform"

# The application invariants this evidence must reproduce (docs/DOMAIN.md §7, ARCHITECTURE.md §6.1).
EXPECT_TABLES=32
EXPECT_POLICIES=122
EXPECT_SCHEMAS=7
EXPECT_ROLES=4

die() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

# Sanitised tail of a captured log, printed only on failure.
show_log() { # $1 = file, $2 = lines
  printf '%s\n' "--- last $2 lines of $(basename "$1") ---" >&2
  tail -n "$2" "$1" | ol_sanitize >&2
  printf '%s\n' '--- end ---' >&2
}

count_matches() { grep -c -E "$1" "$2" || true; }

ol_require_local_target "$ROOT" || die "local target validation failed; nothing was executed"

snapshot_catalogue() { # $1 = output file, $2 = log file
  ol_psql -q -A -t -f - >"$1" 2>"$2" <<'SQL'
select 'role', rolname, rolsuper, rolinherit, rolcanlogin, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication
  from pg_roles where rolname like 'origenlab\_%' order by rolname;
select 'membership', r.rolname, m.rolname, am.inherit_option, am.set_option
  from pg_auth_members am join pg_roles r on r.oid = am.member join pg_roles m on m.oid = am.roleid
 where m.rolname like 'origenlab\_%' or r.rolname like 'origenlab\_%' order by 2, 3, 4, 5;
select 'database_privilege', 'origenlab_owner', p.priv,
       has_database_privilege('origenlab_owner', current_database(), p.priv)
  from (values ('CREATE'), ('CONNECT'), ('TEMPORARY')) p(priv) order by 3;
select 'predefined_membership', m.rolname, r.rolname
  from pg_auth_members am join pg_roles m on m.oid = am.roleid join pg_roles r on r.oid = am.member
 where m.rolname in ('pg_read_all_data', 'pg_write_all_data') order by 2, 3;
select 'schema', nspname, nspowner::regrole::text, nspacl::text from pg_namespace
 where nspname in ('crm','comms','outbound','evidence','catalog','procurement','platform') order by nspname;
select 'default_acl', defaclobjtype, defaclnamespace, defaclacl::text from pg_default_acl
 where defaclrole = 'origenlab_owner'::regrole order by defaclobjtype;
select 'table', n.nspname, c.relname, c.relowner::regrole::text, c.relrowsecurity, c.relforcerowsecurity, c.relacl::text
  from pg_class c join pg_namespace n on n.oid = c.relnamespace
 where c.relkind = 'r' and n.nspname in ('crm','comms','outbound','evidence','catalog','procurement','platform') order by 2, 3;
select 'policy', schemaname, tablename, policyname, roles::text, cmd, permissive, qual, with_check from pg_policies
 where schemaname in ('crm','comms','outbound','evidence','catalog','procurement','platform') order by 2, 3, 4;
select 'function', n.nspname, p.proname, p.proowner::regrole::text, p.prosecdef, p.proconfig::text, p.proacl::text
  from pg_proc p join pg_namespace n on n.oid = p.pronamespace
 where n.nspname in ('crm','comms','outbound','evidence','catalog','procurement','platform') order by 2, 3;
select 'send_control', id, marketing_enabled, transactional_enabled, change_reason from outbound.send_control order by id;
select 'migration', version, name from supabase_migrations.schema_migrations order by version;
SQL
}

for run in 1 2; do
  echo "== run $run: supabase db reset --local =="

  # H1: the reset's exit status is captured and enforced. Output goes to a log; it is never piped
  # into a filter that would discard the status, and it is displayed only after the reset succeeded.
  reset_log="$OUT/reset_run$run.log"
  rc=0
  supabase db reset --local >"$reset_log" 2>&1 || rc=$?
  if (( rc != 0 )); then
    show_log "$reset_log" 40
    die "supabase db reset --local exited $rc on run $run; the replay procedure stops here"
  fi
  grep -E 'Applying migration|Seeding|Finished|Recreating|Restarting' "$reset_log" | ol_sanitize || true

  # H2: the dump's exit status is enforced and its stderr is kept, not discarded.
  dump_log="$OUT/dump_run$run.log"
  rc=0
  supabase db dump --local -s "$SCHEMAS" -f "$OUT/schema_run$run.sql" >"$dump_log" 2>&1 || rc=$?
  if (( rc != 0 )); then
    show_log "$dump_log" 40
    die "supabase db dump --local exited $rc on run $run; the replay procedure stops here"
  fi

  # H2: a successful-but-empty or partial dump must not reach a comparison.
  [[ -s "$OUT/schema_run$run.sql" ]] || {
    show_log "$dump_log" 20
    die "run $run: the schema dump is empty"
  }
  n_tables="$(count_matches '^CREATE TABLE ' "$OUT/schema_run$run.sql")"
  n_policies="$(count_matches '^CREATE POLICY ' "$OUT/schema_run$run.sql")"
  n_schemas="$(count_matches '^CREATE SCHEMA ' "$OUT/schema_run$run.sql")"
  [[ "$n_tables"   -eq "$EXPECT_TABLES"   ]] || die "run $run: dump has $n_tables CREATE TABLE statements, expected $EXPECT_TABLES"
  [[ "$n_policies" -eq "$EXPECT_POLICIES" ]] || die "run $run: dump has $n_policies CREATE POLICY statements, expected $EXPECT_POLICIES"
  [[ "$n_schemas"  -eq "$EXPECT_SCHEMAS"  ]] || die "run $run: dump has $n_schemas CREATE SCHEMA statements, expected $EXPECT_SCHEMAS"
  echo "ok  run $run dump: $n_schemas schemas, $n_tables tables, $n_policies policies"

  # The catalogue snapshot: status enforced, stderr kept, content asserted meaningful.
  cat_log="$OUT/catalogue_run$run.log"
  rc=0
  snapshot_catalogue "$OUT/catalogue_run$run.txt" "$cat_log" || rc=$?
  if (( rc != 0 )); then
    show_log "$cat_log" 20
    die "run $run: the catalogue snapshot exited $rc"
  fi
  [[ -s "$OUT/catalogue_run$run.txt" ]] || die "run $run: the catalogue snapshot is empty"
  c_roles="$(count_matches '^role[|]' "$OUT/catalogue_run$run.txt")"
  c_tables="$(count_matches '^table[|]' "$OUT/catalogue_run$run.txt")"
  c_policies="$(count_matches '^policy[|]' "$OUT/catalogue_run$run.txt")"
  c_schemas="$(count_matches '^schema[|]' "$OUT/catalogue_run$run.txt")"
  [[ "$c_roles"    -eq "$EXPECT_ROLES"    ]] || die "run $run: catalogue lists $c_roles OrigenLab roles, expected $EXPECT_ROLES"
  [[ "$c_schemas"  -eq "$EXPECT_SCHEMAS"  ]] || die "run $run: catalogue lists $c_schemas schemas, expected $EXPECT_SCHEMAS"
  [[ "$c_tables"   -eq "$EXPECT_TABLES"   ]] || die "run $run: catalogue lists $c_tables tables, expected $EXPECT_TABLES"
  [[ "$c_policies" -eq "$EXPECT_POLICIES" ]] || die "run $run: catalogue lists $c_policies policies, expected $EXPECT_POLICIES"
  echo "ok  run $run catalogue: $c_roles roles, $c_schemas schemas, $c_tables tables, $c_policies policies"

  # L4: the applied-migration list is evidence, so its status is enforced and it is asserted
  # non-empty here and compared byte for byte below.
  ml_log="$OUT/migrations_run$run.log"
  rc=0
  supabase migration list --local >"$OUT/migrations_run$run.txt" 2>"$ml_log" || rc=$?
  if (( rc != 0 )); then
    show_log "$ml_log" 20
    die "run $run: supabase migration list --local exited $rc"
  fi
  sed -i 's/\x1b\[[0-9;]*m//g' "$OUT/migrations_run$run.txt"
  [[ -s "$OUT/migrations_run$run.txt" ]] || die "run $run: the applied-migration list is empty"
  local_migrations=("$ROOT"/supabase/migrations/*.sql)
  n_local="${#local_migrations[@]}"
  for v in "${local_migrations[@]}"; do
    version="$(basename "$v")"; version="${version%%_*}"
    grep -q -- "$version" "$OUT/migrations_run$run.txt" \
      || die "run $run: migration $version is missing from the applied-migration list"
  done
  echo "ok  run $run migrations: all $n_local local migrations are recorded as applied"
done

echo
echo "== hashes =="
(cd "$OUT" && sha256sum schema_run1.sql schema_run2.sql catalogue_run1.txt catalogue_run2.txt migrations_run1.txt migrations_run2.txt)
echo

# Equality is tested only now, after every completeness assertion above has passed.
status=0
compare() { # $1 = label, $2 = file a, $3 = file b
  if cmp -s "$2" "$3"; then
    echo "ok  $1 identical across two resets"
  else
    echo "FAIL: $1 differs across two resets" >&2
    diff "$2" "$3" | ol_sanitize | head -40 >&2
    status=1
  fi
}
compare "schema dump"                        "$OUT/schema_run1.sql"     "$OUT/schema_run2.sql"
compare "role/grant/policy catalogue"        "$OUT/catalogue_run1.txt"  "$OUT/catalogue_run2.txt"
compare "applied-migration list"             "$OUT/migrations_run1.txt" "$OUT/migrations_run2.txt"

if [[ $status -ne 0 ]]; then
  echo "replay evidence: NOT reproducible; evidence dir: $OUT" >&2
  exit 1
fi

echo
echo "PASS replay evidence: two resets reproduce identical schema, catalogue and applied-migration list"
echo "     ($EXPECT_SCHEMAS schemas, $EXPECT_TABLES tables, $EXPECT_POLICIES policies, $EXPECT_ROLES OrigenLab roles); evidence dir: $OUT"
