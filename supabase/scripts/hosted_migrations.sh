#!/usr/bin/env bash
# Run reviewed OrigenLab migrations against hosted Supabase. Production-only GitHub Actions entrypoint.
# No automatic runs; GitHub environment approval + an operator-attested backup are required.
set -euo pipefail

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "$-" != *x* ]] || die "shell tracing may expose production credentials"
[[ "${GITHUB_ACTIONS:-}" == "true" ]] || die "GitHub Actions only"
[[ "${GITHUB_REF:-}" == "refs/heads/main" ]] || die "production migrations require main"
[[ $# -eq 1 && ( "$1" == "--plan" || "$1" == "--apply" ) ]] || die "usage: $0 --plan|--apply"
mode="$1"

: "${OL_PROD_POOLER_HOST:?OL_PROD_POOLER_HOST missing}"
: "${OL_PROD_PROJECT_REF:?OL_PROD_PROJECT_REF missing}"
: "${OL_PROD_MIGRATOR_PASSWORD:?OL_PROD_MIGRATOR_PASSWORD missing}"
: "${OL_PROD_LEDGER_PASSWORD:?OL_PROD_LEDGER_PASSWORD missing}"
: "${OL_PROD_CA_PEM:?OL_PROD_CA_PEM missing}"

# Exact endpoint grammar: a Supavisor session pooler for the hosted sa-east-1 project.
[[ "$OL_PROD_POOLER_HOST" =~ ^aws-[0-9]{1,2}-sa-east-1\.pooler\.supabase\.com$ ]] ||
  die "invalid pooler hostname; direct and transaction-mode endpoints are refused"
[[ "$OL_PROD_PROJECT_REF" =~ ^[a-z]{20}$ ]] || die "invalid project reference"

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cd "$root"
[[ -d supabase/migrations ]] || die "migration directory missing"
[[ -n "${RUNNER_TEMP:-}" ]] || die "RUNNER_TEMP missing"
umask 077
cert="$RUNNER_TEMP/origenlab-supabase-ca.pem"
printf '%s\n' "$OL_PROD_CA_PEM" > "$cert"
trap 'rm -f "$cert"' EXIT
openssl x509 -in "$cert" -noout >/dev/null || die "production CA is not a valid X.509 certificate"

export PGHOST="$OL_PROD_POOLER_HOST"
export PGPORT=5432
export PGDATABASE=postgres
export PGSSLMODE=verify-full
export PGSSLROOTCERT="$cert"
export PGCONNECT_TIMEOUT=15

psql_as() {
  local role="$1"
  shift
  case "$role" in
    migrator)
      PGUSER="origenlab_migrator.$OL_PROD_PROJECT_REF" \
      PGPASSWORD="$OL_PROD_MIGRATOR_PASSWORD" \
        psql -X --no-psqlrc -v ON_ERROR_STOP=1 "$@"
      ;;
    ledger)
      PGUSER="postgres.$OL_PROD_PROJECT_REF" \
      PGPASSWORD="$OL_PROD_LEDGER_PASSWORD" \
        psql -X --no-psqlrc -v ON_ERROR_STOP=1 "$@"
      ;;
    *) die "unknown database identity" ;;
  esac
}

# Fail closed before any writes, using two distinct session-pooler logins.
identity="$(psql_as migrator -Atc "select session_user || '|' || pg_has_role(session_user, 'origenlab_owner', 'SET')::text")"
[[ "$identity" == "origenlab_migrator|true" ]] || die "migrator is not authorized to SET ROLE origenlab_owner"
ledger_identity="$(psql_as ledger -Atc "select session_user || '|' || current_database()")"
[[ "$ledger_identity" == "postgres|postgres" ]] || die "ledger identity/database mismatch"

shopt -s nullglob
files=(supabase/migrations/*.sql)
[[ ${#files[@]} -gt 0 ]] || die "no migration files"
versions=()
names=()
last=""
for file in "${files[@]}"; do
  base="${file##*/}"
  [[ "$base" =~ ^([0-9]{14})_([a-z0-9_]+)\.sql$ ]] || die "invalid migration filename: $base"
  version="${BASH_REMATCH[1]}"
  name="${BASH_REMATCH[2]}"
  [[ -z "$last" || "$version" > "$last" ]] || die "migration order not strictly ascending"
  versions+=("$version")
  names+=("$name")
  last="$version"
done

ledger_sql="select version from supabase_migrations.schema_migrations order by version"
ledger_rows="$(psql_as ledger -Atc "$ledger_sql")"
applied=()
if [[ -n "$ledger_rows" ]]; then
  mapfile -t applied <<< "$ledger_rows"
fi
[[ ${#applied[@]} -le ${#versions[@]} ]] || die "hosted ledger has more versions than checkout"
for ((i=0; i<${#applied[@]}; i++)); do
  [[ "${applied[i]}" == "${versions[i]}" ]] ||
    die "production ledger diverges at position $((i+1)); refusing to reconcile automatically"
done

printf 'Hosted ledger: %d / %d reviewed migration files\n' "${#applied[@]}" "${#versions[@]}"
for ((i=${#applied[@]}; i<${#files[@]}; i++)); do
  printf 'PENDING %s\n' "${files[i]##*/}"
done
[[ "$mode" == "--apply" ]] || { echo 'PLAN ONLY: no database writes'; exit 0; }
[[ "${OL_PROD_BACKUP_ATTESTED:-false}" == "true" ]] ||
  die "apply requires operator-attested, restore-checked pre-migration backup"

for ((i=${#applied[@]}; i<${#files[@]}; i++)); do
  printf 'Applying %s under origenlab_migrator...\n' "${files[i]##*/}"
  # Each file is one atomic DDL transaction. Its own SET ROLE cannot be bypassed.
  psql_as migrator --single-transaction -f "${files[i]}" >/dev/null ||
    die "DDL failed; the migration transaction was rolled back; ledger untouched"

  # The Supabase-owned migration ledger is intentionally not writable by origenlab_migrator.
  # Record the completed file with postgres (the existing, separate owner-approved procedure).
  # A failure here leaves schema ahead of the ledger: stop and require operator reconciliation.
  sql="insert into supabase_migrations.schema_migrations (version, name) values ('${versions[i]}', '${names[i]}')"
  psql_as ledger -q -c "$sql" >/dev/null ||
    die "DDL committed but ledger write failed. STOP: inspect schema before any retry"
  confirmed="$(psql_as ledger -Atc "select count(*) from supabase_migrations.schema_migrations where version = '${versions[i]}'")"
  [[ "$confirmed" == "1" ]] || die "ledger verification failed; stop"
  printf 'Applied and recorded %s\n' "${versions[i]}"
done
echo 'Production schema ledger matches the reviewed main migration chain'
