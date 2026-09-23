#!/usr/bin/env bash
# OrigenLab V2 — the persistent local development database.
#
# All V2 work happens against local PostgreSQL 17 while the hosted phase is frozen
# (docs/OPERATIONS.md §1.1), so the local cluster needs a database that survives: one that holds
# the real imported evidence, is never reset, and is recoverable without a hosted backup
# entitlement.
#
# It runs in its OWN container, not in the Supabase CLI's.
#
#   `supabase db reset` drops every non-system database in the CLI's cluster, not only the
#   project database. A persistent database placed there would be destroyed by one routine run
#   of the Slice 0 evidence suite. Measured, not assumed: a reset performed on 2026-09-21 removed
#   `origenlab_dev` and `origenlab_template` from that cluster outright.
#
# So there are two clusters, with one job each:
#
#   * the CLI's cluster        disposable. `supabase db reset` owns it. The pgTAP suite, the
#                              replay evidence, the failure-injection scripts and the
#                              `origenlab_test_*` databases all live there and expect to be wiped.
#   * `origenlab_dev_db`       persistent. Same `supabase/postgres` image, so the same
#                              PostgreSQL 17, the same platform roles and the same extensions —
#                              its own volume, its own loopback-only port, untouched by the CLI.
#
# Two clusters, still one durable database per era: `origenlab_dev` is the local development
# instance of the V2 durable core, and everything in the CLI's cluster is a test fixture.
#
# Durability comes from checkpoints written outside Git, not from the container volume. A
# checkpoint carries DATA ONLY; structure always comes from replaying supabase/migrations/, so
# every restore re-proves the chain and reproduces ownership, grants and RLS from the reviewed
# migrations rather than from a dump.
#
# Every path resolves its target through supabase/scripts/lib/local_target.sh and nothing else,
# so none of it can reach a hosted project.
#
# Usage:
#   supabase/scripts/dev_db.sh up                      # start the container, apply roles.sql
#   supabase/scripts/dev_db.sh down                    # stop it; the volume survives
#   supabase/scripts/dev_db.sh create [--if-not-exists]
#   supabase/scripts/dev_db.sh migrate
#   supabase/scripts/dev_db.sh status
#   supabase/scripts/dev_db.sh checkpoint [--label <text>]
#   supabase/scripts/dev_db.sh restore <checkpoint.sql.gz> --force
#   supabase/scripts/dev_db.sh list
#   supabase/scripts/dev_db.sh api-login                 # local-only DSN for origenlab_api
#   supabase/scripts/dev_db.sh seed-operator <email> [name] [role]
#   supabase/scripts/dev_db.sh destroy --force         # remove the container AND its volume
#
# Procedure: docs/OPERATIONS.md §4.1.

set -euo pipefail

OL_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
export OL_REPO_ROOT
# shellcheck source=lib/local_target.sh
. "$OL_REPO_ROOT/supabase/scripts/lib/local_target.sh"

OL_DEV_DB_NAME="origenlab_dev"

# The image is pinned rather than discovered, so the development database is reproducible even
# when the CLI's stack is not running. It is the image the Supabase CLI itself uses for this
# project; bumping it is a deliberate, reviewed change.
OL_DEV_IMAGE="public.ecr.aws/supabase/postgres:17.6.1.165"
OL_DEV_VOLUME="origenlab_dev_data"

# The private root. Outside Git, referenced by no repository path, covered by no .gitignore
# exemption. Deliberately a sibling of ~/data/origenlab-v2-migration and never the same
# directory, so a checkpoint can never be mistaken for a migration bundle.
OL_PRIVATE_ROOT="${OL_LOCAL_PRIVATE_ROOT:-$HOME/data/origenlab-v2-local}"
OL_CHECKPOINT_DIR="$OL_PRIVATE_ROOT/checkpoints"

die() { echo "FAIL: $*" >&2; exit 1; }
note() { echo "$*" >&2; }

# The seven application schemas. A checkpoint carries their DATA and nothing else.
#
# It deliberately does not carry their structure. A checkpoint is restored onto a schema that
# has just been rebuilt by replaying supabase/migrations/ — so the structure always comes from
# the reviewed migration chain, never from a dump that could drift from it. Three things follow,
# and all three are the point:
#
#   1. every restore re-proves that the chain replays cleanly;
#   2. ownership, grants, RLS and policies are produced by the migrations that were reviewed,
#      not reconstructed by pg_restore — which cannot reproduce them anyway, because every
#      application object is owned by `origenlab_owner` and the connection role holds that
#      membership SET-only;
#   3. it is the same shape as the hosted cutover: apply the proven chain, then replay the data.
#
# The migration ledger is excluded for the same reason: `migrate` owns it, and a restored ledger
# could disagree with the schema actually present.
OL_DUMP_SCHEMAS=(crm comms outbound evidence catalog procurement platform)

ol_dump_schema_args() {
  local s
  for s in "${OL_DUMP_SCHEMAS[@]}"; do printf -- '--schema=%s\n' "$s"; done
}

# pg_dump / pg_restore run *inside* the database container, never on the host.
#
# The host's client tools are whatever the distribution ships — here PostgreSQL 16 against a
# 17.6 server, which pg_dump refuses outright. The container's tools are version-matched to the
# server by construction, so using them removes a whole class of "works on my machine" failure
# and needs no client package on the host.
#
# The connection is the container's own local socket as `postgres`, so no password is passed on
# any command line and nothing appears in host or container process listings. The container is
# the one the dev guard has already proven belongs to this working tree.

# --- up / down / destroy ----------------------------------------------------------------------

cmd_up() {
  command -v docker >/dev/null 2>&1 || die "docker not found"
  local want_root
  want_root="$(cd "$OL_REPO_ROOT" && pwd -P)"

  if docker inspect "$OL_DEV_CONTAINER" >/dev/null 2>&1; then
    local running
    running="$(docker inspect "$OL_DEV_CONTAINER" --format '{{.State.Running}}')"
    if [[ "$running" == "true" ]]; then
      note "note: $OL_DEV_CONTAINER is already running."
    else
      note "starting the existing $OL_DEV_CONTAINER…"
      docker start "$OL_DEV_CONTAINER" >/dev/null || die "could not start $OL_DEV_CONTAINER"
    fi
  else
    note "creating $OL_DEV_CONTAINER from $OL_DEV_IMAGE…"
    # Published on 127.0.0.1 only — deliberately stricter than the CLI's stack, which binds on
    # all interfaces. This database holds real contact data from P2 onward and must not be
    # reachable from the network. The guard refuses the container if this ever changes.
    docker run -d \
      --name "$OL_DEV_CONTAINER" \
      --label "com.origenlab.component=dev-database" \
      --label "com.origenlab.workdir=$want_root" \
      -e POSTGRES_PASSWORD=postgres \
      -e POSTGRES_USER=supabase_admin \
      -e POSTGRES_DB=postgres \
      -e POSTGRES_INITDB_ARGS="--allow-group-access --locale-provider=icu --encoding=UTF-8 --icu-locale=en_US.UTF-8" \
      -p "127.0.0.1:$OL_DEV_PORT:5432" \
      -v "$OL_DEV_VOLUME:/var/lib/postgresql/data" \
      "$OL_DEV_IMAGE" >/dev/null \
      || die "could not create $OL_DEV_CONTAINER"
  fi

  # Readiness is a real query over the published TCP port, not `pg_isready` on the container's
  # socket. This image runs its initdb, accepts socket connections, then restarts before it
  # publishes the port for good — so the socket answers well before the port does, and a
  # socket-only check hands back a database that drops the next connection.
  note "waiting for PostgreSQL to accept TCP connections…"
  local i ready=0
  for i in $(seq 1 90); do
    if PGCONNECT_TIMEOUT=2 psql "postgresql://${OL_DEV_SUPERUSER}:postgres@127.0.0.1:${OL_DEV_PORT}/postgres" \
         -X -q -tAc 'select 1' >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 1
  done
  (( ready )) || die "$OL_DEV_CONTAINER did not accept a TCP connection within 90s"

  ol_require_dev_database "$OL_REPO_ROOT" >/dev/null || die "dev guard refused the container it just started"

  # roles.sql is idempotent and is what the CLI runs before migrations. Applying it here gives
  # this cluster the same four roles, with the same attributes and the same SET-only membership.
  note "applying supabase/roles.sql…"
  ol_psql_dev_maintenance -q -f "$OL_REPO_ROOT/supabase/roles.sql" >/dev/null \
    || die "roles.sql failed"
  note "$OL_DEV_CONTAINER is up on 127.0.0.1:$OL_DEV_PORT."
}

cmd_down() {
  if ! docker inspect "$OL_DEV_CONTAINER" >/dev/null 2>&1; then
    note "note: $OL_DEV_CONTAINER does not exist."
    return 0
  fi
  docker stop "$OL_DEV_CONTAINER" >/dev/null || die "could not stop $OL_DEV_CONTAINER"
  note "$OL_DEV_CONTAINER stopped. Its volume ($OL_DEV_VOLUME) is untouched."
}

cmd_destroy() {
  [[ "${1-}" == "--force" ]] \
    || die "destroy removes $OL_DEV_CONTAINER AND its volume $OL_DEV_VOLUME, discarding every row the last checkpoint does not hold. Take a checkpoint first, then re-run with --force."
  docker rm -f "$OL_DEV_CONTAINER" >/dev/null 2>&1 || true
  docker volume rm "$OL_DEV_VOLUME" >/dev/null 2>&1 || true
  note "removed $OL_DEV_CONTAINER and $OL_DEV_VOLUME. Checkpoints under $OL_CHECKPOINT_DIR are untouched."
}

# --- create ---------------------------------------------------------------------------------

cmd_create() {
  local if_not_exists=0
  [[ "${1-}" == "--if-not-exists" ]] && if_not_exists=1

  ol_require_dev_database "$OL_REPO_ROOT" >/dev/null || die "dev guard refused"

  local exists
  exists="$(ol_psql_dev_maintenance -tAc \
    "select 1 from pg_database where datname = '$OL_DEV_DB_NAME'")" || die "could not query pg_database"

  if [[ -n "$exists" ]]; then
    if (( if_not_exists )); then
      note "note: database $OL_DEV_DB_NAME already exists; leaving it alone."
      return 0
    fi
    die "database $OL_DEV_DB_NAME already exists. Use --if-not-exists, or restore a checkpoint into a fresh one."
  fi

  note "creating database $OL_DEV_DB_NAME…"
  ol_psql_dev_maintenance -c "create database $OL_DEV_DB_NAME" >/dev/null

  ol_require_dev_database "$OL_REPO_ROOT" >/dev/null || die "dev guard refused"
  ol_bootstrap_platform_objects --with-ledger
  note "created $OL_DEV_DB_NAME with the platform-emulating extensions schema and an empty ledger."
}

# Platform emulation, and only platform emulation.
#
# On a hosted Supabase project the `extensions` schema and its trusted extensions are provided
# by the platform before any of our migrations run, and M01's `create extension btree_gist with
# schema extensions` depends on that. A database we create ourselves has no such schema, so we
# reproduce it here — same owner, same ACL as the CLI's own `postgres` database carries —
# rather than editing M01 to tolerate its absence.
#
# This is a local bootstrap step, NOT part of the migration chain. At hosted cutover it is
# simply not run, because the platform has already done it. Keeping it out of
# supabase/migrations/ is what makes the chain replayable against hosted Supabase unmodified.
#
# `--with-ledger` also creates the empty migration ledger. A restore omits it, because the
# checkpoint carries the ledger it must be consistent with.
ol_bootstrap_platform_objects() {
  ol_bootstrap_platform_objects_into ol_psql_dev "$@"
}

# --- migrate --------------------------------------------------------------------------------

cmd_migrate() {
  ol_require_dev_database "$OL_REPO_ROOT" >/dev/null || die "dev guard refused"
  ol_apply_migrations_into ol_psql_dev || die "the migration chain could not be applied"
}

# --- api-login ------------------------------------------------------------------------------

# A password for `origenlab_api` on the DEVELOPMENT CONTAINER ONLY.
#
# `supabase/roles.sql` deliberately assigns no password to any role: a credential in tracked
# content is a credential that leaks. But a local API process has to log in as *some* role, and
# the one role it must not use is a superuser-adjacent login. `postgres` here carries BYPASSRLS,
# so an API reading as `postgres` would silently prove nothing about RLS and would behave
# differently in production than it does locally — the exact class of bug the runtime roles exist
# to prevent.
#
# So the password is generated, never chosen; written 0600 into the private root outside Git;
# and set only on this container, which the dev guard has already proven is loopback-only and
# belongs to this working tree. It is not a secret in the sense that matters — it guards a
# local development database — but it is treated like one anyway, because the habit is what
# keeps a real one from ever being written down.
cmd_api_login() {
  ol_require_dev_database "$OL_REPO_ROOT" >/dev/null || die "dev guard refused"

  mkdir -p "$OL_PRIVATE_ROOT"
  chmod 700 "$OL_PRIVATE_ROOT"
  local env_file="$OL_PRIVATE_ROOT/api.env"

  local password
  password="$(od -An -tx1 -N24 /dev/urandom | tr -d ' \n')"

  # Quoted through psql's own variable interpolation rather than string-pasted into the
  # statement. Interpolation happens only for script input — `-c` sends its argument to the
  # server verbatim — so the statement arrives on stdin.
  ol_psql_dev -q -v pw="$password" >/dev/null <<'SQL' \
    || die "could not set the local password for origenlab_api"
alter role origenlab_api login password :'pw';
SQL

  ( umask 077
    cat > "$env_file" <<EOF
# OrigenLab V2 — local development only. Generated by supabase/scripts/dev_db.sh api-login.
# This file is outside Git and mode 0600. It grants read access to the local development
# database and nothing else. Regenerate at any time; nothing depends on the value.
ORIGENLAB_V2_DATABASE_URL=postgresql://origenlab_api:${password}@127.0.0.1:${OL_DEV_PORT}/${OL_DEV_DBNAME}
EOF
  )
  chmod 600 "$env_file"

  note "origenlab_api can now log in to the development container only."
  note "the DSN was written to:"
  printf '%s\n' "$env_file"
}

# --- seed-operator --------------------------------------------------------------------------

# One `platform.operator` row for local development.
#
# Supabase Auth is migration slice 1 and does not exist yet, so there is no `auth.users` row to
# point at. The `auth_user_id` here is a generated UUID that belongs to no auth system — which
# is honest: it records that this operator was created for local development, not that some
# authenticated identity was verified.
cmd_seed_operator() {
  local email="${1:-}" name="${2:-Operador Local}" role="${3:-admin}"
  [[ -n "$email" ]] || die "seed-operator needs an email address"
  [[ "$email" =~ ^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$ ]] || die "'$email' is not an email address"
  case "$role" in admin|sales|viewer) ;; *) die "role must be admin, sales or viewer" ;; esac

  ol_require_dev_database "$OL_REPO_ROOT" >/dev/null || die "dev guard refused"
  ol_psql_dev -q -v email="${email,,}" -v name="$name" -v role="$role" <<'SQL' >/dev/null \
    || die "could not seed the operator"
set role origenlab_owner;
insert into platform.operator (auth_user_id, email_norm, display_name, role, status)
values (gen_random_uuid(), :'email', :'name', :'role', 'active')
on conflict (email_norm) do nothing;
reset role;
SQL
  note "operator seeded (or already present): $email as $role"
}

# --- status ---------------------------------------------------------------------------------

cmd_status() {
  ol_require_dev_database "$OL_REPO_ROOT" >/dev/null || die "dev guard refused"
  ol_psql_dev <<'SQL'
\echo '== migration ledger =='
select count(*) as recorded, min(version) as first, max(version) as head
from supabase_migrations.schema_migrations;

\echo '== application tables by schema =='
select schemaname, count(*) as tables
from pg_stat_user_tables
where schemaname in ('crm','comms','outbound','evidence','catalog','procurement','platform')
group by 1 order by 1;

\echo '== rows by schema =='
select schemaname, coalesce(sum(n_live_tup), 0) as approx_rows
from pg_stat_user_tables
where schemaname in ('crm','comms','outbound','evidence','catalog','procurement','platform')
group by 1 order by 1;

\echo '== send control (both must be false) =='
select * from outbound.send_control;
SQL
}

# --- checkpoint -----------------------------------------------------------------------------

cmd_checkpoint() {
  local label=''
  if [[ "${1-}" == "--label" ]]; then
    label="${2-}"
    [[ -n "$label" ]] || die "--label needs a value"
    [[ "$label" =~ ^[A-Za-z0-9._-]{1,40}$ ]] || die "--label must be 1-40 characters of [A-Za-z0-9._-]"
  fi

  ol_require_dev_database "$OL_REPO_ROOT" >/dev/null || die "dev guard refused"

  mkdir -p "$OL_CHECKPOINT_DIR"
  chmod 700 "$OL_PRIVATE_ROOT" "$OL_CHECKPOINT_DIR"

  local stamp out tmp
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  out="$OL_CHECKPOINT_DIR/${stamp}_${OL_DEV_DB_NAME}${label:+_$label}.sql.gz"
  tmp="$out.partial"

  note "writing checkpoint…"
  # A checkpoint holds real contact data from P2 onward, so it is written 0600 into the private
  # root and its path — never its contents — is what this script prints.
  local -a schema_args
  mapfile -t schema_args < <(ol_dump_schema_args)

  # Plain SQL, gzipped — not pg_dump's custom format.
  #
  # The schema has 102 non-deferrable foreign keys and several genuine cycles (the supersession
  # and merge chains are self-referential; opportunity↔quote↔quote_revision↔send_attempt close a
  # loop). A data-only reload therefore cannot be ordered into validity and must suppress foreign
  # key triggers for the duration, which means `set session_replication_role = replica`.
  #
  # That setting can only be applied *after* connecting: the connection role may set it, but the
  # server refuses it as a connection-time parameter, and pg_restore offers no way to inject a
  # statement before its own. A plain stream can simply carry it as its first line, so that is
  # what a checkpoint is. `pg_restore --disable-triggers` is not an alternative — it needs
  # superuser, and the connection role is not one.
  ( umask 077
    docker exec "$OL_DEV_CONTAINER" \
      pg_dump -U postgres -d "$OL_DEV_DB_NAME" --data-only --format=plain \
        "${schema_args[@]}" 2>/dev/null | gzip -9 > "$tmp" ) \
    || { rm -f "$tmp"; die "pg_dump failed; no checkpoint was written"; }

  # Assert the dump is real before it is blessed with a sidecar. An empty or truncated dump that
  # a later restore would silently accept is the failure this guards against.
  [[ -s "$tmp" ]] || { rm -f "$tmp"; die "pg_dump produced an empty file; no checkpoint was written"; }
  gzip -t "$tmp" 2>/dev/null \
    || { rm -f "$tmp"; die "pg_dump produced a file gzip cannot read; no checkpoint was written"; }
  # The header is captured, not piped into `grep -q`. Under `pipefail`, a `grep -q` that exits
  # on the first match SIGPIPEs gunzip and the pipeline reports gunzip's failure — which looks
  # exactly like a corrupt dump. A small dump hides it, because gunzip finishes first; a real one
  # does not.
  local header
  header="$(gunzip -c "$tmp" 2>/dev/null | head -n 20 || true)"
  [[ "$header" == *"PostgreSQL database dump"* ]] \
    || { rm -f "$tmp"; die "the compressed file is not a PostgreSQL dump; no checkpoint was written"; }

  mv "$tmp" "$out"
  chmod 600 "$out"
  ( cd "$OL_CHECKPOINT_DIR" && sha256sum "$(basename "$out")" > "$(basename "$out").sha256" )
  chmod 600 "$out.sha256"

  # The metadata sidecar records the migration head this data was captured against, so a restore
  # can say plainly whether it is replaying the data onto the same chain or a later one. It holds
  # counts and versions only — never a value from a row.
  ( umask 077
    ol_psql_dev -tAc "
      select json_build_object(
        'database', current_database(),
        'captured_at', to_char(now() at time zone 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'),
        'migration_count', (select count(*) from supabase_migrations.schema_migrations),
        'migration_head', (select max(version) from supabase_migrations.schema_migrations),
        'row_counts', (
          select coalesce(json_object_agg(t, n), '{}'::json) from (
            select schemaname || '.' || relname as t, n_live_tup as n
            from pg_stat_user_tables
            where schemaname in ('crm','comms','outbound','evidence','catalog','procurement','platform')
              and n_live_tup > 0
            order by 1
          ) s
        )
      )::text" > "$out.meta.json" ) \
    || { note "warning: the checkpoint was written but its metadata sidecar could not be built"; }
  [[ -f "$out.meta.json" ]] && chmod 600 "$out.meta.json"

  note "checkpoint written:"
  printf '%s\n' "$out"
}

# --- restore --------------------------------------------------------------------------------

cmd_restore() {
  local file='' force=0 arg
  for arg in "$@"; do
    case "$arg" in
      --force) force=1 ;;
      -*)      die "restore: unknown option '$arg'" ;;
      *)       [[ -z "$file" ]] || die "restore takes one checkpoint path"; file="$arg" ;;
    esac
  done
  [[ -n "$file" ]] || die "restore needs a checkpoint path"
  [[ -f "$file" ]] || die "checkpoint $file not found"
  [[ -f "$file.sha256" ]] || die "checkpoint $file has no .sha256 sidecar; refusing to restore an unverified dump"

  note "verifying checkpoint integrity…"
  ( cd "$(dirname "$file")" && sha256sum --check --status "$(basename "$file").sha256" ) \
    || die "checkpoint $file does not match its .sha256 sidecar; refusing to restore"

  ol_require_dev_database "$OL_REPO_ROOT" >/dev/null || die "dev guard refused"

  # A restore REPLACES the development database. It is not additive and it is not reversible,
  # so it is refused without --force.
  #
  # The database is dropped and recreated rather than restored with `pg_restore --clean` into
  # the existing one. `--clean` emits DROP statements that the connection role cannot execute:
  # every application object is owned by `origenlab_owner`, `postgres` holds that membership
  # SET-only, and pg_restore never issues a `set role`. Dropping the database sidesteps that
  # entirely and gives a genuinely clean restore rather than a partially-cleaned one.
  if [[ "$force" != "1" ]]; then
    die "restore replaces $OL_DEV_DB_NAME entirely and cannot be undone. Take a checkpoint first, then re-run with --force."
  fi

  if [[ -f "$file.meta.json" ]]; then
    note "checkpoint metadata: $(cat "$file.meta.json")"
  else
    note "note: this checkpoint has no metadata sidecar; the chain it was captured against is unknown."
  fi

  note "rebuilding $OL_DEV_DB_NAME from the migration chain…"
  ol_psql_dev_maintenance -c \
    "select pg_terminate_backend(pid) from pg_stat_activity where datname = '$OL_DEV_DB_NAME' and pid <> pg_backend_pid()" \
    >/dev/null || die "could not terminate existing sessions on $OL_DEV_DB_NAME"
  ol_psql_dev_maintenance -c "drop database if exists $OL_DEV_DB_NAME" >/dev/null \
    || die "could not drop $OL_DEV_DB_NAME"
  ol_psql_dev_maintenance -c "create database $OL_DEV_DB_NAME" >/dev/null \
    || die "could not recreate $OL_DEV_DB_NAME"

  ol_require_dev_database "$OL_REPO_ROOT" >/dev/null || die "dev guard refused"
  ol_bootstrap_platform_objects --with-ledger
  cmd_migrate

  # Clear what the migrations themselves seeded — `outbound.send_control` is one row — so the
  # checkpoint's data is reinstated exactly as captured rather than colliding with it. Run as
  # `origenlab_owner`, which owns every one of these tables.
  note "clearing migration-seeded rows before replaying the checkpoint…"
  ol_psql_dev <<'SQL' >/dev/null || die "could not clear the seeded rows"
set role origenlab_owner;
do $$
declare stmt text;
begin
  select 'truncate table ' || string_agg(format('%I.%I', schemaname, tablename), ', ') || ' cascade'
    into stmt
  from pg_tables
  where schemaname in ('crm','comms','outbound','evidence','catalog','procurement','platform');
  if stmt is not null then execute stmt; end if;
end
$$;
reset role;
SQL

  note "replaying checkpoint data…"
  # The prelude is the whole reason a checkpoint is a plain stream, and the order of its two
  # statements matters:
  #
  #   1. `session_replication_role = replica` suppresses the foreign-key triggers, which the
  #      schema's genuine cycles make unavoidable. Only the connection role may set it.
  #   2. `set role origenlab_owner` then loads the rows as the role that owns every one of these
  #      tables, so the load does not quietly depend on the connection role's BYPASSRLS.
  #
  # One transaction with ON_ERROR_STOP, so a failure commits nothing and leaves the bare chain.
  { printf 'set session_replication_role = replica;\nset role origenlab_owner;\n'
    gunzip -c "$file"; } \
    | docker exec -i "$OL_DEV_CONTAINER" \
        psql -U postgres -d "$OL_DEV_DB_NAME" -X -q -v ON_ERROR_STOP=1 --single-transaction -f - \
    || die "the checkpoint replay failed; nothing was committed — $OL_DEV_DB_NAME holds the bare chain"
  note "restored."
}

# --- list -----------------------------------------------------------------------------------

cmd_list() {
  if [[ ! -d "$OL_CHECKPOINT_DIR" ]]; then
    note "no checkpoints yet ($OL_CHECKPOINT_DIR does not exist)."
    return 0
  fi
  find "$OL_CHECKPOINT_DIR" -maxdepth 1 -name '*.sql.gz' -type f -printf '%TY-%Tm-%Td %TH:%TM  %10s  %p\n' \
    | sort
}

# --- dispatch -------------------------------------------------------------------------------

main() {
  local cmd="${1-}"
  shift || true
  case "$cmd" in
    up)         cmd_up "$@" ;;
    down)       cmd_down "$@" ;;
    destroy)    cmd_destroy "$@" ;;
    create)        cmd_create "$@" ;;
    api-login)     cmd_api_login "$@" ;;
    seed-operator) cmd_seed_operator "$@" ;;
    migrate)    cmd_migrate "$@" ;;
    status)     cmd_status "$@" ;;
    checkpoint) cmd_checkpoint "$@" ;;
    restore)    cmd_restore "$@" ;;
    list)       cmd_list "$@" ;;
    ''|-h|--help)
      sed -n '2,48p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      ;;
    *) die "unknown subcommand '$cmd'. Run with --help." ;;
  esac
}

main "$@"
