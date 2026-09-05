#!/usr/bin/env bash
# OrigenLab V2 — Slice 0 deterministic reset/replay evidence.
#
# Resets the local database from roles.sql + migrations twice, dumps the seven application schemas
# (DDL, constraints, indexes, grants, policies, comments), snapshots the role/membership/default-
# privilege catalogue (no passwords are readable or written), and proves both runs are identical by
# SHA-256. Output directory: $1 or a fresh temp dir. Prints the hashes; exits non-zero on any diff.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

OUT="${1:-$(mktemp -d)}"
mkdir -p "$OUT"
SCHEMAS="crm,comms,outbound,evidence,catalog,procurement,platform"

snapshot_catalogue() { # $1 = output file
  local db_url
  db_url="$(supabase status -o env 2>/dev/null | sed -n 's/^DB_URL=//p' | tr -d '"')"
  psql "$db_url" -X -q -A -t -v ON_ERROR_STOP=1 -f - >"$1" <<'SQL'
select 'role', rolname, rolsuper, rolinherit, rolcanlogin, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication
  from pg_roles where rolname like 'origenlab\_%' order by rolname;
select 'membership', r.rolname, m.rolname, am.inherit_option, am.set_option
  from pg_auth_members am join pg_roles r on r.oid = am.member join pg_roles m on m.oid = am.roleid
 where m.rolname like 'origenlab\_%' or r.rolname like 'origenlab\_%' order by 2, 3, 4, 5;
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
  supabase db reset --local 2>&1 | grep -E 'Applying migration|Seeding globals|Finished|Recreating' || true
  supabase db dump --local -s "$SCHEMAS" -f "$OUT/schema_run$run.sql" >/dev/null 2>&1
  snapshot_catalogue "$OUT/catalogue_run$run.txt"
  supabase migration list --local 2>/dev/null | sed 's/\x1b\[[0-9;]*m//g' >"$OUT/migrations_run$run.txt" || true
done

echo
echo "== hashes =="
(cd "$OUT" && sha256sum schema_run1.sql schema_run2.sql catalogue_run1.txt catalogue_run2.txt)
echo
status=0
cmp -s "$OUT/schema_run1.sql" "$OUT/schema_run2.sql" && echo "PASS schema dump identical across two resets" || { echo "FAIL schema dump differs"; diff "$OUT/schema_run1.sql" "$OUT/schema_run2.sql" | head -40; status=1; }
cmp -s "$OUT/catalogue_run1.txt" "$OUT/catalogue_run2.txt" && echo "PASS role/grant/policy catalogue identical across two resets" || { echo "FAIL catalogue differs"; diff "$OUT/catalogue_run1.txt" "$OUT/catalogue_run2.txt" | head -40; status=1; }
echo "tables in dump: $(grep -c -E '^CREATE TABLE ' "$OUT/schema_run1.sql")   policies in dump: $(grep -c -E '^CREATE POLICY ' "$OUT/schema_run1.sql")   evidence dir: $OUT"
exit $status
