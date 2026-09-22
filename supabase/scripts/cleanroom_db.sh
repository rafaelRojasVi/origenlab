#!/usr/bin/env bash
# OrigenLab V2 — the clean-room database.
#
# `origenlab_clean`: a database rebuilt from nothing but the reviewed migration chain and the
# reproducible historical load, so that everything in it can be traced to an input a human can
# read and refuse.
#
# WHY IT EXISTS
#
#   On 2026-09-22 a database-backed test run wrote into `origenlab_dev`: seven fixture source
#   records, seven fixture operators, nine command receipts, and the three organizations, two
#   contact points and sixteen domain events those commands produced. The same run applied
#   migration 20260922090000's DDL without recording its ledger row, so `origenlab_dev` no
#   longer describes its own schema.
#
#   `origenlab_dev` also holds twenty real staged Gmail records. That makes it neither
#   trustworthy nor disposable — so it is quarantined, and this is the database we work in
#   instead. Rebuilding is not repairing: nothing here reads, writes, drops, restores over or
#   otherwise opens `origenlab_dev`, and the guard discards its DSN before this script runs a
#   single statement.
#
# THE NAME IS A LITERAL
#
#   This script drops a database. The one fact that must never be in doubt is which one, so the
#   name comes from a constant in supabase/scripts/lib/local_target.sh and from nowhere else —
#   not an argument, not an environment variable, not a config file. `build --force` prints the
#   exact name immediately before the DROP, and supabase/scripts/cleanroom_failure_tests.sh
#   proves the guard refuses to resolve to anything but `origenlab_clean`.
#
# WHAT IT NEVER DOES
#
#   No Gmail, Drive or hosted Supabase connection; no campaign, no send, no quote. The three
#   Python tools it drives import no Google client and their own target boundary accepts a
#   literal loopback address only. No audit trigger is disabled, bypassed or altered.
#
# Usage:
#   supabase/scripts/cleanroom_db.sh build [--force]   # rebuild from migrations + historical load
#   supabase/scripts/cleanroom_db.sh verify            # read-only; compare against the baseline
#   supabase/scripts/cleanroom_db.sh status
#   supabase/scripts/cleanroom_db.sh api-login         # local-only DSN for origenlab_api
#   supabase/scripts/cleanroom_db.sh drop --force
#
# Procedure: docs/OPERATIONS.md §4.1 (“The clean-room database”).

set -euo pipefail

OL_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
export OL_REPO_ROOT
# shellcheck source=lib/local_target.sh
. "$OL_REPO_ROOT/supabase/scripts/lib/local_target.sh"

OL_PRIVATE_ROOT="${OL_LOCAL_PRIVATE_ROOT:-$HOME/data/origenlab-v2-local}"
OL_MIGRATION_ROOT="${OL_MIGRATION_ROOT:-$HOME/data/origenlab-v2-migration}"
# Every staging manifest the build replays, and the only place it looks for them. The build
# stages *the whole directory*, in sorted order, rather than one named file: R1 (docs/STATUS.md
# §2.7.11) proved that naming a single manifest silently makes every later batch unreproducible —
# the clean room rebuilt without it and nobody could see what had gone. A manifest is in the
# baseline when it is in this directory, and `verify` fails loudly if that changes the counts,
# so a stray file cannot slip in unnoticed either. The files themselves stay outside Git.
OL_EVIDENCE_MANIFEST_DIR="$OL_PRIVATE_ROOT/evidence"
OL_PIPELINE_DIR="$OL_REPO_ROOT/apps/email-pipeline"
OL_CLEANROOM_DIR="$OL_REPO_ROOT/supabase/cleanroom"

# The operator seeded into a fresh build. One row, so the API command boundary can resolve an
# identity at all; `auth_user_id` belongs to no auth system, because Supabase Auth is slice 1.
#
# The address is **fictitious by default** and undeliverable (RFC 2606 `.invalid`). This
# repository is public, and a developer's own mailbox is personal data that has no business
# being a constant in it -- it used to be one, and the privacy guard
# (apps/email-pipeline/tests/test_public_repo_privacy_hygiene.py) now fails if it becomes one
# again. Nothing depends on the value: the seed exists so that one identity resolves, and
# `verify` counts the row rather than reading its address.
#
# Override it for a local build with the environment, which is not committed:
#
#   OL_CLEAN_OPERATOR_EMAIL=you@yourdomain.cl supabase/scripts/cleanroom_db.sh build --force
#
# Unlike the database name, this one is deliberately overridable: it names nothing that can be
# dropped, so an environment variable here cannot cause the loss the name constant prevents.
OL_CLEAN_OPERATOR_EMAIL="${OL_CLEAN_OPERATOR_EMAIL:-operador.local@example.invalid}"
OL_CLEAN_OPERATOR_NAME="${OL_CLEAN_OPERATOR_NAME:-Operador Local}"
OL_CLEAN_OPERATOR_ROLE="admin"

die() { echo "FAIL: $*" >&2; exit 1; }
note() { echo "$*" >&2; }
step() { echo "" >&2; echo "── $* ────────────────────────────────────────" >&2; }

# The DSN handed to the three Python tools. Built from the guard's validated parts, never from
# an argument. Each tool re-validates it through its own loopback boundary before connecting.
clean_dsn() {
  [[ -n "${OL_CLEAN_DB_URL:-}" ]] || die "clean_dsn called before the clean-room guard succeeded"
  printf '%s' "$OL_CLEAN_DB_URL"
}

# --- preflight --------------------------------------------------------------------------------

# Everything the build needs, verified before anything is dropped or created.
#
# Existence is not enough. A build that drops the database, applies the chain and only then
# discovers that a manifest no longer loads leaves a half-loaded database that looks real — and
# on 2026-09-22 that is exactly what happened: migration 20260922090000 raised the manifest
# schema to version 2, the September file was still version 1, and the clean room could not be
# rebuilt from its own declared inputs. So every input is put through the same validation the
# build will later apply to it, *first*, while a failure still costs nothing.
#
# Both checks below run the real tools in their dry-run mode, which opens no database
# connection at all. Preflight therefore cannot reach $OL_CLEAN_DBNAME, let alone change it:
# it runs before the guard, and a refusal here returns with the existing database untouched.
cleanroom_preflight() {
  command -v docker >/dev/null 2>&1 || die "docker not found"
  command -v psql   >/dev/null 2>&1 || die "psql not found"
  command -v uv     >/dev/null 2>&1 || die "uv not found; the historical load runs under it"
  command -v python3 >/dev/null 2>&1 || die "python3 not found"

  [[ -d "$OL_REPO_ROOT/supabase/migrations" ]] || die "supabase/migrations/ not found"
  [[ -f "$OL_CLEANROOM_DIR/verify.sql" ]] || die "supabase/cleanroom/verify.sql not found"
  [[ -f "$OL_CLEANROOM_DIR/expected_counts.json" ]] || die "supabase/cleanroom/expected_counts.json not found"

  [[ -d "$OL_MIGRATION_ROOT" ]] \
    || die "the Wave 1A/1B migration root $OL_MIGRATION_ROOT does not exist. It is outside Git by design; the clean room can only be built on a machine that holds it."
  [[ -d "$OL_EVIDENCE_MANIFEST_DIR" ]] \
    || die "the staging manifest directory $OL_EVIDENCE_MANIFEST_DIR does not exist. It is outside Git by design and is never re-fetched: this tooling makes no Gmail connection."
  local -a manifests=()
  cleanroom_read_manifests manifests
  (( ${#manifests[@]} )) \
    || die "no staging manifest found in $OL_EVIDENCE_MANIFEST_DIR. The clean room can only be built on a machine that holds them."

  step "PREFLIGHT — every input validated before anything is dropped"

  cleanroom_preflight_artifacts
  local manifest
  for manifest in "${manifests[@]}"; do
    cleanroom_preflight_manifest "$manifest"
  done

  note "preflight passed: the Wave 1A/1B artifacts and all ${#manifests[@]} staging manifest(s)."
  note "no database connection has been opened."
}

# The Wave 1A/1B artifacts, through the importer's own dry run: it re-verifies each bundle's
# manifest and every file hash, so a truncated, edited or absent artifact refuses here rather
# than half-way through a load.
cleanroom_preflight_artifacts() {
  local out rc=0
  out="$( cd "$OL_PIPELINE_DIR" && uv run python scripts/migration/import_waves_into_v2.py \
      --migration-root "$OL_MIGRATION_ROOT" --json 2>&1 )" || rc=$?
  if (( rc != 0 )); then
    printf '%s\n' "$out" | tail -n 20 >&2
    die "the Wave 1A/1B artifacts under $OL_MIGRATION_ROOT did not verify. Nothing was dropped."
  fi
  note "  ok  Wave 1A/1B artifacts verified (import dry run, no connection opened)"
}

# One staging manifest, through the staging tool's own dry run. Same parser, same rules, same
# refusals as the apply pass — a manifest that passes here is one the build can replay.
cleanroom_preflight_manifest() {
  local manifest="$1" out rc=0
  out="$( cd "$OL_PIPELINE_DIR" && uv run python scripts/migration/stage_gmail_drive_evidence.py \
      --manifest "$manifest" --json 2>&1 )" || rc=$?
  if (( rc != 0 )); then
    printf '%s\n' "$out" | tail -n 10 >&2
    die "the staging manifest $(basename "$manifest") is not loadable. Nothing was dropped; fix or withdraw the file and run the build again."
  fi
  local counts
  counts="$(printf '%s' "$out" | python3 -c '
import json, sys
m = json.load(sys.stdin)["manifest"]
print("%d record(s), %d observation(s)" % (m["records"], m["observations"]))
')" || die "could not read the dry-run report for $(basename "$manifest")"
  note "  ok  $(basename "$manifest") — $counts"
}

# --- build ------------------------------------------------------------------------------------

cmd_build() {
  local force=0 arg
  for arg in "$@"; do
    case "$arg" in
      --force) force=1 ;;
      *) die "build: unknown option '$arg'" ;;
    esac
  done

  cleanroom_preflight
  ol_require_cleanroom_database "$OL_REPO_ROOT" || die "clean-room guard refused"

  local exists
  exists="$(ol_psql_clean_maintenance -tAc \
    "select 1 from pg_database where datname = '$OL_CLEAN_DBNAME'")" \
    || die "could not query pg_database"

  if [[ -n "$exists" ]]; then
    (( force )) || die "database $OL_CLEAN_DBNAME already exists. A build replaces it entirely; re-run with --force if that is what you want."

    # The required announcement. The literal name, on its own line, immediately before the DROP
    # and after every other check has passed — so the last thing an operator reads before a
    # database disappears is which database it was.
    step "DROPPING THE CLEAN-ROOM DATABASE"
    note "about to drop this database, and only this database:"
    printf '%s\n' "$OL_CLEAN_DBNAME" >&2
    note "(origenlab_dev is not touched: the guard discarded its DSN before this script began.)"

    ol_psql_clean_maintenance -c \
      "select pg_terminate_backend(pid) from pg_stat_activity where datname = '$OL_CLEAN_DBNAME' and pid <> pg_backend_pid()" \
      >/dev/null || die "could not terminate existing sessions on $OL_CLEAN_DBNAME"
    ol_psql_clean_maintenance -c "drop database if exists $OL_CLEAN_DBNAME" >/dev/null \
      || die "could not drop $OL_CLEAN_DBNAME"
    note "dropped $OL_CLEAN_DBNAME."
  fi

  step "CREATING $OL_CLEAN_DBNAME"
  ol_psql_clean_maintenance -c "create database $OL_CLEAN_DBNAME" >/dev/null \
    || die "could not create $OL_CLEAN_DBNAME"
  ol_bootstrap_platform_objects_into ol_psql_clean --with-ledger \
    || die "could not bootstrap the platform-emulating objects"
  note "created, with the extensions schema and an empty ledger."

  step "APPLYING THE MIGRATION CHAIN"
  ol_apply_migrations_into ol_psql_clean || die "the migration chain could not be applied"

  step "SEEDING ONE LOCAL OPERATOR"
  cleanroom_seed_operator

  step "HISTORICAL LOAD — Wave 1A/1B safety artifacts into evidence.* and outbound.*"
  ( cd "$OL_PIPELINE_DIR" && uv run python scripts/migration/import_waves_into_v2.py \
      --migration-root "$OL_MIGRATION_ROOT" \
      --apply --database-url "$(clean_dsn)" ) \
    || die "the historical import refused or failed; $OL_CLEAN_DBNAME holds the bare chain"

  step "PROMOTION — evidence.* into crm.*"
  ( cd "$OL_PIPELINE_DIR" && uv run python scripts/migration/promote_evidence_into_crm.py \
      --database-url "$(clean_dsn)" --apply ) \
    || die "the promotion refused or failed"

  step "STAGING MANIFESTS — each replayed exactly once, from local files, with no network call"
  local -a manifests=() manifest
  cleanroom_read_manifests manifests
  note "${#manifests[@]} manifest(s) in $OL_EVIDENCE_MANIFEST_DIR, staged in this order:"
  for manifest in "${manifests[@]}"; do note "  $(basename "$manifest")"; done
  for manifest in "${manifests[@]}"; do
    ( cd "$OL_PIPELINE_DIR" && uv run python scripts/migration/stage_gmail_drive_evidence.py \
        --manifest "$manifest" \
        --database-url "$(clean_dsn)" --apply ) \
      || die "staging refused or failed on $(basename "$manifest"); $OL_CLEAN_DBNAME is partially loaded and must be rebuilt, not patched"
  done

  step "VERIFYING"
  cleanroom_verify
}

# The manifests to replay, sorted by name, into the caller's array. Sorted rather than
# directory order so two machines holding the same files replay them in the same sequence.
cleanroom_read_manifests() {
  local -n _out="$1"
  _out=()
  local f
  while IFS= read -r -d '' f; do _out+=("$f"); done \
    < <(find "$OL_EVIDENCE_MANIFEST_DIR" -maxdepth 1 -type f -name '*.json' -print0 | sort -z)
}

# One `platform.operator` row, as `origenlab_owner` — the role that owns the table. `on conflict
# do nothing`, so a rebuild is idempotent and a second run never mints a second identity.
cleanroom_seed_operator() {
  ol_psql_clean -q \
    -v email="$OL_CLEAN_OPERATOR_EMAIL" \
    -v name="$OL_CLEAN_OPERATOR_NAME" \
    -v role="$OL_CLEAN_OPERATOR_ROLE" <<'SQL' >/dev/null || die "could not seed the operator"
set role origenlab_owner;
insert into platform.operator (auth_user_id, email_norm, display_name, role, status)
values (gen_random_uuid(), :'email', :'name', :'role', 'active')
on conflict (email_norm) do nothing;
reset role;
SQL
  note "operator seeded (or already present): $OL_CLEAN_OPERATOR_EMAIL as $OL_CLEAN_OPERATOR_ROLE"
}

# --- verify -----------------------------------------------------------------------------------

# Read-only. verify.sql runs inside `begin read only`, so this can be run at any time against a
# database in any state without being able to change it.
cleanroom_verify() {
  ol_psql_clean -q -f "$OL_CLEANROOM_DIR/verify.sql" \
    | python3 "$OL_CLEANROOM_DIR/compare.py" "$OL_CLEANROOM_DIR/expected_counts.json" \
    || die "the clean-room database does not match its declared expected state"
}

cmd_verify() {
  cleanroom_preflight
  ol_require_cleanroom_database "$OL_REPO_ROOT" || die "clean-room guard refused"
  cleanroom_verify
}

# --- status -----------------------------------------------------------------------------------

cmd_status() {
  ol_require_cleanroom_database "$OL_REPO_ROOT" || die "clean-room guard refused"
  ol_psql_clean <<'SQL'
\echo '== migration ledger =='
select count(*) as recorded, min(version) as first, max(version) as head
from supabase_migrations.schema_migrations;

\echo '== rows by schema =='
select schemaname, coalesce(sum(n_live_tup), 0) as approx_rows
from pg_stat_user_tables
where schemaname in ('crm','comms','outbound','evidence','catalog','procurement','platform')
group by 1 order by 1;

\echo '== send control (both must be false) =='
select marketing_enabled, transactional_enabled from outbound.send_control;
SQL
}

# --- api-login --------------------------------------------------------------------------------

# A DSN for `origenlab_api` against the CLEAN-ROOM database, written to its own file.
#
# It is deliberately a second file rather than an edit of api.env: `dev_db.sh api-login` keeps
# writing api.env for `origenlab_dev`, so the default target is unchanged and selecting the
# clean room is an explicit act — you say which file to source. Nothing switches by itself.
cmd_api_login() {
  ol_require_cleanroom_database "$OL_REPO_ROOT" || die "clean-room guard refused"

  mkdir -p "$OL_PRIVATE_ROOT"
  chmod 700 "$OL_PRIVATE_ROOT"
  local env_file="$OL_PRIVATE_ROOT/api.cleanroom.env"

  local password
  password="$(od -An -tx1 -N24 /dev/urandom | tr -d ' \n')"

  # `alter role` is cluster-wide, so this sets the password `origenlab_api` uses for every
  # database in this container. That is unavoidable and harmless: the role is unprivileged, the
  # container is loopback-only, and which database it reaches is decided by the DSN below.
  ol_psql_clean -q -v pw="$password" >/dev/null <<'SQL' \
    || die "could not set the local password for origenlab_api"
alter role origenlab_api login password :'pw';
SQL

  ( umask 077
    cat > "$env_file" <<EOF
# OrigenLab V2 — the CLEAN-ROOM database. Local development only.
# Generated by supabase/scripts/cleanroom_db.sh api-login. Outside Git, mode 0600.
#
# Sourcing this file points apps/api at origenlab_clean instead of the default origenlab_dev.
# That is the only switch there is; apps/dashboard reaches the database only through the API.
ORIGENLAB_V2_DATABASE_URL=postgresql://origenlab_api:${password}@127.0.0.1:${OL_CLEAN_DB_PORT}/${OL_CLEAN_DBNAME}
EOF
  )
  chmod 600 "$env_file"

  note "origenlab_api can now log in to the clean-room database."
  note "the DSN was written to:"
  printf '%s\n' "$env_file"
}

# --- drop -------------------------------------------------------------------------------------

cmd_drop() {
  [[ "${1-}" == "--force" ]] \
    || die "drop removes $OL_CLEAN_DBNAME entirely. It is rebuildable with \`build\`, but say so explicitly: re-run with --force."

  ol_require_cleanroom_database "$OL_REPO_ROOT" || die "clean-room guard refused"

  step "DROPPING THE CLEAN-ROOM DATABASE"
  note "about to drop this database, and only this database:"
  printf '%s\n' "$OL_CLEAN_DBNAME" >&2

  ol_psql_clean_maintenance -c \
    "select pg_terminate_backend(pid) from pg_stat_activity where datname = '$OL_CLEAN_DBNAME' and pid <> pg_backend_pid()" \
    >/dev/null || die "could not terminate existing sessions on $OL_CLEAN_DBNAME"
  ol_psql_clean_maintenance -c "drop database if exists $OL_CLEAN_DBNAME" >/dev/null \
    || die "could not drop $OL_CLEAN_DBNAME"
  note "dropped $OL_CLEAN_DBNAME. Rebuild it with: supabase/scripts/cleanroom_db.sh build"
}

# --- dispatch ---------------------------------------------------------------------------------

main() {
  local cmd="${1-}"
  shift || true
  case "$cmd" in
    build)     cmd_build "$@" ;;
    verify)    cmd_verify "$@" ;;
    status)    cmd_status "$@" ;;
    api-login) cmd_api_login "$@" ;;
    drop)      cmd_drop "$@" ;;
    ''|-h|--help)
      sed -n '2,42p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      ;;
    *) die "unknown subcommand '$cmd'. Run with --help." ;;
  esac
}

main "$@"
