#!/usr/bin/env bash
# OrigenLab V2 — structural verification of an applied migration chain.
#
# Answers one question about any local database: does it carry exactly the foundation the
# reviewed migrations define? Schema, roles, grants, default privileges, RLS, policies, indexes,
# functions and triggers are each measured from the catalogue and compared against a committed
# expectation.
#
# This complements the pgTAP suite rather than duplicating it. pgTAP proves semantics — that a
# constraint rejects what it should, that a definer call sees the right `current_user`. This
# proves inventory: that what is actually present matches what the chain is supposed to produce,
# on a database pgTAP was not run against, such as the persistent `origenlab_dev` or a restored
# checkpoint.
#
# Read-only. It opens one transaction, sets it read-only, and runs SELECTs.
#
# Usage:
#   supabase/scripts/verify_chain.sh [--dev | --database <name>] [--json]
#
# --dev (the default) measures the persistent development database in its own container.
# --database <name> measures a database in the Supabase CLI's cluster: `postgres`, or a minted
# `origenlab_test_<8 hex>`. --json prints the measured report to stdout; without it a human
# summary goes to stderr. Exit status is non-zero on any mismatch.
#
# Expectations: docs/DOMAIN.md §7 and §7.1 (the 36-table inventory), docs/STATUS.md §2.1 (measured),
# docs/MIGRATION.md §5.2 (the privilege proofs). Procedure: docs/OPERATIONS.md §4.1.

set -euo pipefail

OL_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
export OL_REPO_ROOT
# shellcheck source=lib/local_target.sh
. "$OL_REPO_ROOT/supabase/scripts/lib/local_target.sh"

# The committed expectation. These are the numbers docs/DOMAIN.md §7 and docs/STATUS.md §2.1
# carry; a migration that changes one of them changes those documents in the same PR, and this
# file with them. They are deliberately not derived from the database — a check that measures
# its own expectation proves nothing.
EXPECT_SCHEMAS=7
EXPECT_TABLES=36
EXPECT_TABLES_BY_SCHEMA="catalog=2,comms=4,crm=19,evidence=2,outbound=6,platform=2,procurement=1"
EXPECT_POLICIES=139
EXPECT_ROLES=4
EXPECT_SECURITY_DEFINER=0

OL_SCHEMAS_SQL="'crm','comms','outbound','evidence','catalog','procurement','platform'"

# Default to the persistent development database, which lives in its own container.
TARGET="dev"
DATABASE=""
AS_JSON=0
while (( $# )); do
  case "$1" in
    --dev)      TARGET="dev"; shift ;;
    --database) TARGET="cli"; DATABASE="${2-}"; shift 2 ;;
    --json)     AS_JSON=1; shift ;;
    -h|--help)  sed -n '2,24p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "FAIL: unknown option '$1'" >&2; exit 2 ;;
  esac
done

die() { echo "FAIL: $*" >&2; exit 1; }
note() { echo "$*" >&2; }

FAILURES=0
check() {
  local label="$1" got="$2" want="$3"
  if [[ "$got" == "$want" ]]; then
    note "  ok    $label: $got"
  else
    note "  FAIL  $label: measured '$got', expected '$want'"
    FAILURES=$(( FAILURES + 1 ))
  fi
}

# Two clusters, two guards, one verifier. `--dev` measures the persistent development database
# in its own container; `--database <name>` measures a database in the Supabase CLI's cluster —
# its `postgres` project database, or a minted `origenlab_test_<8 hex>`.
if [[ "$TARGET" == "dev" ]]; then
  ol_require_dev_database "$OL_REPO_ROOT" >/dev/null || die "dev guard refused"
  VERIFY_URL="$OL_DEV_DB_URL"
  VERIFY_LABEL="$OL_DEV_DBNAME (dev container)"
else
  ol_require_local_database "$DATABASE" "$OL_REPO_ROOT" >/dev/null || die "database guard refused"
  VERIFY_URL="$OL_TARGET_DB_URL"
  VERIFY_LABEL="$DATABASE (CLI cluster)"
fi

# One read-only transaction for every measurement, so the verifier cannot mutate what it
# inspects even by accident. `-q` keeps psql's BEGIN/COMMIT command tags out of the value.
q() { psql "$VERIFY_URL" -X -q -tA -v ON_ERROR_STOP=1 -c "begin read only; $1; commit;" | sed '/^$/d'; }

note "verify_chain: $VERIFY_LABEL"

# --- migrations -------------------------------------------------------------------------------
chain_files="$(find "$OL_REPO_ROOT/supabase/migrations" -maxdepth 1 -name '*.sql' -type f -printf '%f\n' | sort | cut -d_ -f1 | tr '\n' ',')"
ledger="$(q "select string_agg(version, ',' order by version) from supabase_migrations.schema_migrations")"
check "migration ledger matches supabase/migrations/" "$ledger" "${chain_files%,}"

# --- schemas ----------------------------------------------------------------------------------
check "schemas" "$(q "select count(*) from pg_namespace where nspname in ($OL_SCHEMAS_SQL)")" "$EXPECT_SCHEMAS"

# --- tables -----------------------------------------------------------------------------------
check "tables" "$(q "select count(*) from pg_tables where schemaname in ($OL_SCHEMAS_SQL)")" "$EXPECT_TABLES"
check "tables by schema" \
  "$(q "select string_agg(schemaname || '=' || n, ',' order by schemaname) from (select schemaname, count(*) n from pg_tables where schemaname in ($OL_SCHEMAS_SQL) group by 1) s")" \
  "$EXPECT_TABLES_BY_SCHEMA"

# --- roles ------------------------------------------------------------------------------------
check "OrigenLab roles" \
  "$(q "select count(*) from pg_roles where rolname like 'origenlab\\_%'")" "$EXPECT_ROLES"
# MIGRATION.md §5.2 check 1: no OrigenLab role bypasses RLS, and none carries a cluster power.
check "roles with a forbidden attribute" \
  "$(q "select count(*) from pg_roles where rolname like 'origenlab\\_%' and (rolbypassrls or rolsuper or rolcreaterole or rolcreatedb or rolreplication)")" "0"
check "origenlab_owner is NOLOGIN" \
  "$(q "select rolcanlogin::text from pg_roles where rolname = 'origenlab_owner'")" "false"

# --- grant boundary (MIGRATION.md §5.2 checks 3-4) ---------------------------------------------
check "schema USAGE held by PUBLIC/anon/authenticated/service_role" \
  "$(q "select count(*) from pg_namespace n, unnest(array['public','anon','authenticated','service_role']) g where n.nspname in ($OL_SCHEMAS_SQL) and has_schema_privilege(g, n.nspname, 'USAGE')")" "0"
check "table privileges held by those roles" \
  "$(q "select count(*) from information_schema.role_table_grants where table_schema in ($OL_SCHEMAS_SQL) and grantee in ('PUBLIC','anon','authenticated','service_role')")" "0"
check "function EXECUTE held by those roles" \
  "$(q "select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace, unnest(array['public','anon','authenticated','service_role']) g where n.nspname in ($OL_SCHEMAS_SQL) and has_function_privilege(g, p.oid, 'EXECUTE')")" "0"

# --- default privileges (MIGRATION.md §5.2 check 5) --------------------------------------------
check "default privileges granting those roles anything" \
  "$(q "select count(*) from pg_default_acl d join pg_namespace n on n.oid = d.defaclnamespace, unnest(coalesce(d.defaclacl, '{}'::aclitem[])) a where n.nspname in ($OL_SCHEMAS_SQL) and (a::text like 'anon=%' or a::text like 'authenticated=%' or a::text like 'service_role=%' or a::text like '=%')")" "0"

# --- database-level CREATE (M14) ---------------------------------------------------------------
check "origenlab_owner holds CREATE on the database" \
  "$(q "select has_database_privilege('origenlab_owner', current_database(), 'CREATE')::text")" "false"
check "origenlab_owner holds CONNECT on the database" \
  "$(q "select has_database_privilege('origenlab_owner', current_database(), 'CONNECT')::text")" "true"

# --- RLS and policies ---------------------------------------------------------------------------
check "tables without RLS enabled" \
  "$(q "select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname in ($OL_SCHEMAS_SQL) and c.relkind = 'r' and not c.relrowsecurity")" "0"
check "RLS policies" \
  "$(q "select count(*) from pg_policies where schemaname in ($OL_SCHEMAS_SQL)")" "$EXPECT_POLICIES"

# --- indexes ------------------------------------------------------------------------------------
# Every foreign key must be index-covered: some index's leading columns must be the constraint's
# referencing columns. An unindexed referencing column is a structural cost, not an artefact of
# an empty database.
check "foreign keys without a covering index" \
  "$(q "
    select count(*) from pg_constraint fk
    join pg_class c on c.oid = fk.conrelid
    join pg_namespace n on n.oid = c.relnamespace
    where fk.contype = 'f' and n.nspname in ($OL_SCHEMAS_SQL)
      and not exists (
        select 1 from pg_index i
        where i.indrelid = fk.conrelid
          and (i.indkey::smallint[])[0:array_length(fk.conkey,1)-1] = fk.conkey
      )")" "0"

# --- functions and triggers -----------------------------------------------------------------------
check "SECURITY DEFINER functions" \
  "$(q "select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname in ($OL_SCHEMAS_SQL) and p.prosecdef")" "$EXPECT_SECURITY_DEFINER"
check "functions not owned by origenlab_owner" \
  "$(q "select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname in ($OL_SCHEMAS_SQL) and pg_get_userbyid(p.proowner) <> 'origenlab_owner'")" "0"
check "tables not owned by origenlab_owner" \
  "$(q "select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace where n.nspname in ($OL_SCHEMAS_SQL) and c.relkind in ('r','v','m','S') and pg_get_userbyid(c.relowner) <> 'origenlab_owner'")" "0"

# Triggers are enumerated rather than counted against a magic number: the list is the evidence,
# and a change to it is visible in the report and in review.
TRIGGERS="$(q "
  select coalesce(string_agg(n.nspname || '.' || c.relname || '.' || t.tgname, ','
                            order by n.nspname, c.relname, t.tgname), '')
  from pg_trigger t
  join pg_class c on c.oid = t.tgrelid
  join pg_namespace n on n.oid = c.relnamespace
  where n.nspname in ($OL_SCHEMAS_SQL) and not t.tgisinternal")"
note "  note  user triggers: ${TRIGGERS:-<none>}"

# --- send control -----------------------------------------------------------------------------
check "send flags both false" \
  "$(q "select (marketing_enabled or transactional_enabled)::text from outbound.send_control where id = 1")" "false"

# --- report -----------------------------------------------------------------------------------
if (( AS_JSON )); then
  q "select json_build_object(
      'database', current_database(),
      'verified_at', to_char(now() at time zone 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'),
      'migrations', (select count(*) from supabase_migrations.schema_migrations),
      'migration_head', (select max(version) from supabase_migrations.schema_migrations),
      'schemas', (select count(*) from pg_namespace where nspname in ($OL_SCHEMAS_SQL)),
      'tables', (select count(*) from pg_tables where schemaname in ($OL_SCHEMAS_SQL)),
      'policies', (select count(*) from pg_policies where schemaname in ($OL_SCHEMAS_SQL)),
      'foreign_keys', (select count(*) from pg_constraint fk join pg_class c on c.oid = fk.conrelid join pg_namespace n on n.oid = c.relnamespace where fk.contype = 'f' and n.nspname in ($OL_SCHEMAS_SQL)),
      'security_definer_functions', (select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname in ($OL_SCHEMAS_SQL) and p.prosecdef),
      'rows_by_schema', (select coalesce(json_object_agg(schemaname, n), '{}'::json) from (select schemaname, sum(n_live_tup) n from pg_stat_user_tables where schemaname in ($OL_SCHEMAS_SQL) group by 1) s)
    )::text"
fi

if (( FAILURES )); then
  note "verify_chain: $FAILURES check(s) FAILED on $VERIFY_LABEL"
  exit 1
fi
note "verify_chain: all checks passed on $VERIFY_LABEL"
