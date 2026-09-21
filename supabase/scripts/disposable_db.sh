#!/usr/bin/env bash
# OrigenLab V2 — disposable PostgreSQL 17 databases for migration, integration, rollback,
# concurrency and deterministic-replay tests.
#
# A test that needs its own database gets one here rather than borrowing the CLI's `postgres`
# database (which `supabase db reset` owns) or the persistent `origenlab_dev` (which holds real
# imported evidence). Each one carries the full, unmodified migration chain and is thrown away.
#
# Databases are cloned from `origenlab_template` rather than migrated one by one, so a
# concurrency test that wants six databases costs six CREATE DATABASE statements instead of six
# chain replays. The template is rebuilt automatically whenever its ledger drifts from
# supabase/migrations/, so it can never silently serve a stale schema.
#
# Every path resolves its target through supabase/scripts/lib/local_target.sh and nothing else.
# `drop` and `sweep` refuse any name outside `origenlab_test_<8 hex>`, so neither can remove
# `postgres`, `origenlab_dev` or the template.
#
# Usage:
#   supabase/scripts/disposable_db.sh mint            # prints the new database name on stdout
#   supabase/scripts/disposable_db.sh drop <name>
#   supabase/scripts/disposable_db.sh sweep
#   supabase/scripts/disposable_db.sh template-status
#   supabase/scripts/disposable_db.sh template-rebuild
#
# Typical use:
#   db="$(supabase/scripts/disposable_db.sh mint)"
#   trap 'supabase/scripts/disposable_db.sh drop "$db"' EXIT
#
# Procedure: docs/OPERATIONS.md §4.1.

set -euo pipefail

OL_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
export OL_REPO_ROOT
# shellcheck source=lib/local_target.sh
. "$OL_REPO_ROOT/supabase/scripts/lib/local_target.sh"

OL_TEMPLATE_DB="origenlab_template"

die() { echo "FAIL: $*" >&2; exit 1; }
note() { echo "$*" >&2; }

# The head of supabase/migrations/ — the version the template must carry to be current.
ol_chain_head() {
  find "$OL_REPO_ROOT/supabase/migrations" -maxdepth 1 -name '*.sql' -type f -printf '%f\n' \
    | sort | tail -n 1 | cut -d_ -f1
}

ol_chain_count() {
  find "$OL_REPO_ROOT/supabase/migrations" -maxdepth 1 -name '*.sql' -type f | wc -l | tr -d ' '
}

ol_db_exists() {
  local name="$1" out
  out="$(ol_psql_maintenance -tAc "select 1 from pg_database where datname = '$name'")" \
    || die "could not query pg_database"
  [[ -n "$out" ]]
}

ol_drop_db() {
  local name="$1"
  ol_psql_maintenance -c \
    "select pg_terminate_backend(pid) from pg_stat_activity where datname = '$name' and pid <> pg_backend_pid()" \
    >/dev/null || die "could not terminate sessions on $name"
  ol_psql_maintenance -c "drop database if exists $name" >/dev/null \
    || die "could not drop $name"
}

# --- template -------------------------------------------------------------------------------

# The template is current when its ledger holds exactly the chain on disk. Anything else — a
# missing database, a short ledger, a different head — means rebuild. Comparing the head alone
# would miss a migration inserted before the last one, so the count is compared too.
ol_template_is_current() {
  ol_db_exists "$OL_TEMPLATE_DB" || return 1
  ol_require_local_database "$OL_TEMPLATE_DB" "$OL_REPO_ROOT" >/dev/null || return 1
  local head count
  head="$(ol_psql_db -tAc "select coalesce(max(version), '') from supabase_migrations.schema_migrations" 2>/dev/null)" || return 1
  count="$(ol_psql_db -tAc "select count(*) from supabase_migrations.schema_migrations" 2>/dev/null)" || return 1
  [[ "$head" == "$(ol_chain_head)" && "$count" == "$(ol_chain_count)" ]]
}

ol_template_build() {
  note "building $OL_TEMPLATE_DB from the migration chain…"
  ol_db_exists "$OL_TEMPLATE_DB" && ol_drop_db "$OL_TEMPLATE_DB"
  ol_psql_maintenance -c "create database $OL_TEMPLATE_DB" >/dev/null \
    || die "could not create $OL_TEMPLATE_DB"

  ol_require_local_database "$OL_TEMPLATE_DB" "$OL_REPO_ROOT" >/dev/null || die "database guard refused"

  # The same platform emulation dev_db.sh performs, for the same reason: on a hosted project the
  # `extensions` schema and its trusted extensions exist before any migration runs. This is a
  # local bootstrap step and is deliberately not part of the chain.
  ol_psql_db <<'SQL' >/dev/null || die "could not bootstrap the template's platform objects"
create schema if not exists extensions authorization postgres;
grant usage on schema extensions to anon, authenticated, service_role;
grant usage, create on schema extensions to dashboard_user;
create extension if not exists btree_gist with schema extensions;
create schema if not exists supabase_migrations authorization postgres;
create table if not exists supabase_migrations.schema_migrations (
  version text not null primary key,
  statements text[],
  name text
);
SQL

  local file base version name
  while IFS= read -r file; do
    base="$(basename "$file")"
    version="${base%%_*}"
    name="${base#*_}"; name="${name%.sql}"
    ol_psql_db --single-transaction -f "$file" >/dev/null \
      || die "migration $base failed while building the template"
    ol_psql_db -tAc \
      "insert into supabase_migrations.schema_migrations (version, name) values ('$version', '$name')" \
      >/dev/null || die "could not record $base in the template's ledger"
  done < <(find "$OL_REPO_ROOT/supabase/migrations" -maxdepth 1 -name '*.sql' -type f | sort)

  note "template built at head $(ol_chain_head) ($(ol_chain_count) migrations)."
}

ol_template_ensure() {
  if ol_template_is_current; then return 0; fi
  ol_template_build
}

# --- commands -------------------------------------------------------------------------------

cmd_mint() {
  ol_require_local_target "$OL_REPO_ROOT" >/dev/null || die "target guard refused"
  ol_template_ensure

  local name attempt=0
  while :; do
    name="origenlab_test_$(od -An -tx1 -N4 /dev/urandom | tr -d ' \n')"
    ol_db_exists "$name" || break
    attempt=$(( attempt + 1 ))
    (( attempt < 8 )) || die "could not find an unused disposable database name"
  done

  # Cloning requires no other session on the template, which is why nothing is ever left
  # connected to it.
  ol_psql_maintenance -c "create database $name template $OL_TEMPLATE_DB" >/dev/null \
    || die "could not clone $OL_TEMPLATE_DB into $name"

  note "minted $name from $OL_TEMPLATE_DB at head $(ol_chain_head)."
  # The name, and only the name, on stdout — so callers can capture it.
  printf '%s\n' "$name"
}

cmd_drop() {
  local name="${1-}"
  # The one thing this command must never do is drop something that is not disposable. The
  # pattern is checked here, before the guard, so the refusal is unconditional.
  [[ "$name" =~ ^origenlab_test_[0-9a-f]{8}$ ]] \
    || die "'${name:-<empty>}' is not a disposable database. Only origenlab_test_<8 hex> may be dropped; postgres, origenlab_dev and origenlab_template never can."

  ol_require_local_target "$OL_REPO_ROOT" >/dev/null || die "target guard refused"
  if ! ol_db_exists "$name"; then
    note "note: $name does not exist; nothing to drop."
    return 0
  fi
  ol_drop_db "$name"
  note "dropped $name."
}

cmd_sweep() {
  ol_require_local_target "$OL_REPO_ROOT" >/dev/null || die "target guard refused"
  local names n=0
  names="$(ol_psql_maintenance -tAc \
    "select datname from pg_database where datname ~ '^origenlab_test_[0-9a-f]{8}\$' order by 1")" \
    || die "could not list disposable databases"
  [[ -n "$names" ]] || { note "no disposable databases to sweep."; return 0; }
  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    ol_drop_db "$name"
    n=$(( n + 1 ))
  done <<<"$names"
  note "swept $n disposable database(s)."
}

cmd_template_status() {
  ol_require_local_target "$OL_REPO_ROOT" >/dev/null || die "target guard refused"
  if ol_template_is_current; then
    note "$OL_TEMPLATE_DB is current at head $(ol_chain_head) ($(ol_chain_count) migrations)."
  else
    note "$OL_TEMPLATE_DB is absent or stale; the next mint rebuilds it. Chain head is $(ol_chain_head)."
  fi
}

cmd_template_rebuild() {
  ol_require_local_target "$OL_REPO_ROOT" >/dev/null || die "target guard refused"
  ol_template_build
}

main() {
  local cmd="${1-}"
  shift || true
  case "$cmd" in
    mint)             cmd_mint "$@" ;;
    drop)             cmd_drop "$@" ;;
    sweep)            cmd_sweep "$@" ;;
    template-status)  cmd_template_status "$@" ;;
    template-rebuild) cmd_template_rebuild "$@" ;;
    ''|-h|--help)
      sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      ;;
    *) die "unknown subcommand '$cmd'. Run with --help." ;;
  esac
}

main "$@"
