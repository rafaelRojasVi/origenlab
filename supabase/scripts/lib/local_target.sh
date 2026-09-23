#!/usr/bin/env bash
# OrigenLab V2 — Slice 0 local-target guard. This file is SOURCED, never executed.
#
# Every script that opens a psql connection resolves its target through `ol_require_local_target`
# and nothing else. The guard is fail-closed: it refuses an empty or unparseable URL, a non-loopback
# host, a port or project that is not this worktree's local Supabase project, a stack whose
# container does not belong to *this* working tree, any sign that the project is linked to a hosted
# project, and a failed `supabase status`. It returns non-zero *before* any connection is attempted,
# so a validation failure can never be followed by a psql call.
#
# Four independent facts must agree before a connection is opened:
#
#   1. `supabase/config.toml` names this project and this database port;
#   2. the project is **not linked** — no `supabase/.temp/project-ref`, no hosted project
#      identifier, and no inherited `SUPABASE_*` variable that could redirect a command at a
#      hosted project or authenticate against one;
#   3. the running `supabase_db_<project>` container carries
#      `com.supabase.cli.workdir` equal to *this* working tree, so a stack started from another
#      checkout of this monorepo is refused rather than silently reused;
#   4. the URL `supabase status` reports is loopback, on that same port, and parses.
#
# It also scrubs the inherited libpq environment. PGHOST/PGPORT/PGUSER/PGDATABASE (and the rest of
# the PG* family) would otherwise silently redirect a psql invocation that omits a parameter, so
# they are unset rather than trusted: an inherited environment must not be able to redirect any
# command this repository runs. There is deliberately no fallback to them.
#
# Nothing here prints a password, a key or a complete connection string. Diagnostics name at most
# the role, host, port and database.
#
# Design: docs/ARCHITECTURE.md §6, §6.4. Procedure: docs/OPERATIONS.md §4.1.

# shellcheck shell=bash

# The pin. These are asserted against supabase/config.toml, which is the second, independent
# source: a URL is accepted only when both agree and the URL matches them.
OL_EXPECTED_PROJECT_ID="origenlab"
OL_EXPECTED_DB_PORT="54322"

# The Supabase environment variables that could point a command at a hosted project or
# authenticate against one. Like the libpq family they are unset rather than trusted.
OL_HOSTED_ENV_VARS=(
  SUPABASE_ACCESS_TOKEN SUPABASE_PROJECT_REF SUPABASE_PROJECT_ID SUPABASE_DB_URL
  SUPABASE_DB_PASSWORD SUPABASE_URL SUPABASE_ANON_KEY SUPABASE_SERVICE_ROLE_KEY
)

# The libpq environment variables that can redirect or re-authenticate a connection.
OL_PG_ENV_VARS=(
  PGHOST PGHOSTADDR PGPORT PGUSER PGDATABASE PGPASSWORD PGPASSFILE PGSERVICE PGSERVICEFILE
  PGOPTIONS PGSSLMODE PGREQUIRESSL PGSSLROOTCERT PGSSLCERT PGSSLKEY PGCHANNELBINDING
  PGTARGETSESSIONATTRS PGCONNECT_TIMEOUT PGAPPNAME PGCLIENTENCODING PGREQUIREPEER PGGSSENCMODE
)

# Redact anything that looks like a connection string or a password from a diagnostic excerpt.
ol_sanitize() {
  sed -E \
    -e 's#(postgres(ql)?://)[^[:space:]"'"'"']*#\1[redacted]#g' \
    -e 's#(password|PGPASSWORD)([[:space:]]*[=:][[:space:]]*)[^[:space:]]+#\1\2[redacted]#Ig'
}

# Unset every libpq variable. Names that were set are reported (names only, never values), because
# silently ignoring a hostile environment is worse evidence than saying it was ignored.
ol_scrub_pg_env() {
  local v
  local -a present=()
  for v in "${OL_PG_ENV_VARS[@]}" "${OL_HOSTED_ENV_VARS[@]}"; do
    if [[ -v "$v" ]]; then
      present+=("$v")
      unset "$v"
    fi
  done
  if (( ${#present[@]} > 0 )); then
    printf 'note: ignoring inherited libpq/Supabase environment (%s); the target comes only from `supabase status`\n' \
      "${present[*]}" >&2
  fi
}

# ol_config_value <section> <key> < config.toml
# <section> is the empty string for a top-level key. Prints the unquoted value, or nothing.
ol_config_value() {
  awk -v want_section="$1" -v want_key="$2" '
    { line = $0 }
    line ~ /^[[:space:]]*#/ { next }
    line ~ /^[[:space:]]*\[/ {
      sub(/^[[:space:]]*\[/, "", line); sub(/\][[:space:]]*$/, "", line)
      section = line; next
    }
    {
      if (section != want_section) next
      if (line !~ "^[[:space:]]*" want_key "[[:space:]]*=") next
      sub("^[[:space:]]*" want_key "[[:space:]]*=[[:space:]]*", "", line)
      sub(/[[:space:]]*#.*$/, "", line)
      gsub(/^"|"$/, "", line)
      gsub(/[[:space:]]+$/, "", line)
      print line; exit
    }
  '
}

# ol_require_local_target [repo root]
# On success exports OL_DB_URL (never printed), OL_DB_USER, OL_DB_HOST, OL_DB_PORT, OL_DB_NAME and
# OL_HOSTPORT. On failure prints a diagnostic and returns non-zero having opened no connection.
ol_require_local_target() {
  local root="${1:-${OL_REPO_ROOT:-$PWD}}"
  local cfg="$root/supabase/config.toml"

  ol_scrub_pg_env

  if [[ ! -f "$cfg" ]]; then
    echo "FAIL: target guard: $cfg not found; refusing to connect." >&2
    return 1
  fi

  local cfg_project cfg_port
  cfg_project="$(ol_config_value '' project_id <"$cfg")"
  cfg_port="$(ol_config_value db port <"$cfg")"
  if [[ "$cfg_project" != "$OL_EXPECTED_PROJECT_ID" ]]; then
    echo "FAIL: target guard: config.toml project_id is '${cfg_project:-<unset>}', expected '$OL_EXPECTED_PROJECT_ID'; refusing to connect." >&2
    return 1
  fi
  if [[ "$cfg_port" != "$OL_EXPECTED_DB_PORT" ]]; then
    echo "FAIL: target guard: config.toml [db].port is '${cfg_port:-<unset>}', expected '$OL_EXPECTED_DB_PORT'; refusing to connect." >&2
    return 1
  fi

  # linked_project must be null. The CLI records a link as supabase/.temp/project-ref; a hosted
  # project reference anywhere in config.toml is refused for the same reason. Nothing here reaches
  # the network to ask — absence of the link is the proof, and a present link is fatal.
  local ref_file="$root/supabase/.temp/project-ref"
  if [[ -e "$ref_file" ]]; then
    echo "FAIL: target guard: $ref_file exists — this project is linked to a hosted project; refusing to connect." >&2
    echo "FAIL: linked_project must be null for every command in this repository (\`supabase unlink\`)." >&2
    return 1
  fi
  local cfg_ref
  cfg_ref="$(ol_config_value '' project_ref <"$cfg")"
  if [[ -n "$cfg_ref" ]]; then
    echo "FAIL: target guard: config.toml declares a hosted project_ref; refusing to connect." >&2
    return 1
  fi

  # The running container must belong to *this* working tree. Two checkouts of this monorepo
  # share the project id, so the id alone cannot distinguish them — the CLI's workdir label can.
  if ! command -v docker >/dev/null 2>&1; then
    echo "FAIL: target guard: docker not found, so the container's working tree cannot be proven; refusing to connect." >&2
    return 1
  fi
  local want_root container labels_rc=0 label_workdir label_project
  want_root="$(cd "$root" && pwd -P)"
  container="supabase_db_${OL_EXPECTED_PROJECT_ID}"
  label_workdir="$(docker inspect "$container" --format '{{index .Config.Labels "com.supabase.cli.workdir"}}' 2>&1)" || labels_rc=$?
  if (( labels_rc != 0 )); then
    echo "FAIL: target guard: cannot inspect container '$container' — the local stack is not running from this working tree; refusing to connect." >&2
    printf '%s\n' "$label_workdir" | ol_sanitize | tail -n 3 >&2
    return 1
  fi
  if [[ "$label_workdir" != "$want_root" ]]; then
    echo "FAIL: target guard: container '$container' was started from '${label_workdir:-<unset>}', not from this working tree ('$want_root'); refusing to connect." >&2
    return 1
  fi
  label_project="$(docker inspect "$container" --format '{{index .Config.Labels "com.supabase.cli.project"}}' 2>/dev/null || true)"
  if [[ "$label_project" != "$OL_EXPECTED_PROJECT_ID" ]]; then
    echo "FAIL: target guard: container '$container' carries project label '${label_project:-<unset>}', expected '$OL_EXPECTED_PROJECT_ID'; refusing to connect." >&2
    return 1
  fi

  local status_out status_rc=0
  status_out="$( cd "$root" && supabase status -o env 2>&1 )" || status_rc=$?
  if (( status_rc != 0 )); then
    echo "FAIL: target guard: \`supabase status\` exited $status_rc — the local stack is not running." >&2
    printf '%s\n' "$status_out" | ol_sanitize | tail -n 5 >&2
    echo "FAIL: no fallback to the libpq environment exists; refusing to connect." >&2
    return 1
  fi

  local url
  url="$(sed -n 's/^DB_URL=//p' <<<"$status_out" | tr -d '"' | head -n 1)"
  if [[ -z "$url" ]]; then
    echo "FAIL: target guard: \`supabase status\` reported no DB_URL; refusing to connect." >&2
    return 1
  fi

  # Strict parse. Anything this regex does not match is refused rather than guessed at.
  local re='^postgres(ql)?://([^:@/?#]+)(:([^@/]*))?@(\[[0-9A-Fa-f:]+\]|[A-Za-z0-9._-]+):([0-9]{1,5})/([A-Za-z0-9_$-]+)(\?.*)?$'
  if [[ ! "$url" =~ $re ]]; then
    echo "FAIL: target guard: the DB_URL from \`supabase status\` did not parse; refusing to connect." >&2
    return 1
  fi
  local user="${BASH_REMATCH[2]}" host="${BASH_REMATCH[5]}" port="${BASH_REMATCH[6]}" dbname="${BASH_REMATCH[7]}"

  case "$host" in
    127.0.0.1|localhost|'[::1]'|'::1') ;;
    *)
      echo "FAIL: target guard: host '$host' is not a loopback address; refusing to connect." >&2
      return 1
      ;;
  esac

  if [[ "$port" != "$OL_EXPECTED_DB_PORT" ]]; then
    echo "FAIL: target guard: port $port is not this project's local database port ($OL_EXPECTED_DB_PORT); refusing to connect." >&2
    return 1
  fi

  OL_DB_URL="$url"; OL_DB_USER="$user"; OL_DB_HOST="$host"; OL_DB_PORT="$port"; OL_DB_NAME="$dbname"
  OL_HOSTPORT="$host:$port"
  export OL_DB_URL OL_DB_USER OL_DB_HOST OL_DB_PORT OL_DB_NAME OL_HOSTPORT
  printf 'local target: project %s, role %s at %s/%s\n' "$OL_EXPECTED_PROJECT_ID" "$user" "$OL_HOSTPORT" "$dbname"
  printf 'local target: linked_project null, container workdir %s, loopback host, port %s\n' \
    "$want_root" "$OL_DB_PORT"
  return 0
}

# psql against the validated target. Refuses if the guard has not run.
ol_psql() {
  if [[ -z "${OL_DB_URL:-}" ]]; then
    echo "FAIL: ol_psql called before ol_require_local_target succeeded; refusing to connect." >&2
    return 1
  fi
  psql "$OL_DB_URL" -X -v ON_ERROR_STOP=1 "$@"
}

# ---------------------------------------------------------------------------
# Named databases inside the validated local cluster.
#
# The cluster holds three classes of database, and every one of them is reached through the
# same four-fact guard above:
#
#   * `postgres`             the Supabase CLI's project database. Disposable: `supabase db reset`
#                            owns it, and the pgTAP suite and the evidence scripts run there.
#   * `origenlab_dev`        the persistent development database. Never reset, checkpointed to a
#                            private root outside Git.
#   * `origenlab_template`  the build template the disposable databases are cloned from. Holds
#                            the chain and nothing else; rebuilt whenever it drifts from
#                            supabase/migrations/.
#   * `origenlab_test_<8hex>` disposable per-run databases for rollback, concurrency and replay
#                            tests that must not disturb either of the above.
#
# Procedure: docs/OPERATIONS.md §4.1.

# The closed set of database names this repository will connect to. A name that would need
# quoting is a name we did not mint, so it is refused rather than escaped: this is what stops a
# crafted name from smuggling a host, a query string or a second connection parameter into a DSN.
ol_valid_local_dbname() {
  local name="${1-}"
  [[ "$name" == "postgres" ]] && return 0
  [[ "$name" == "origenlab_dev" ]] && return 0
  [[ "$name" == "origenlab_template" ]] && return 0
  [[ "$name" =~ ^origenlab_test_[0-9a-f]{8}$ ]] && return 0
  return 1
}

# ol_require_local_database <dbname> [repo root]
# Re-proves every fact `ol_require_local_target` proves, then substitutes the database name.
# On success exports OL_TARGET_DB_NAME and OL_TARGET_DB_URL (never printed).
ol_require_local_database() {
  local name="${1-}"
  if ! ol_valid_local_dbname "$name"; then
    echo "FAIL: local database guard: '${name:-<empty>}' is not a database this repository mints; refusing to connect." >&2
    return 1
  fi
  ol_require_local_target "${2:-${OL_REPO_ROOT:-$PWD}}" || return 1

  # Rebuild the DSN from the parts the guard validated rather than string-editing the URL. The
  # only value carried over from `supabase status` is the password, and it is never printed.
  local pw=''
  if [[ "$OL_DB_URL" =~ ^postgres(ql)?://[^:@/]+:([^@/]*)@ ]]; then
    pw="${BASH_REMATCH[2]}"
  fi
  OL_TARGET_DB_NAME="$name"
  OL_TARGET_DB_URL="postgresql://${OL_DB_USER}:${pw}@${OL_DB_HOST}:${OL_DB_PORT}/${name}"
  export OL_TARGET_DB_NAME OL_TARGET_DB_URL
  printf 'local database: role %s at %s/%s\n' "$OL_DB_USER" "$OL_HOSTPORT" "$name"
  return 0
}

# psql against the validated named database. Refuses if the guard has not run.
ol_psql_db() {
  if [[ -z "${OL_TARGET_DB_URL:-}" ]]; then
    echo "FAIL: ol_psql_db called before ol_require_local_database succeeded; refusing to connect." >&2
    return 1
  fi
  psql "$OL_TARGET_DB_URL" -X -v ON_ERROR_STOP=1 "$@"
}

# psql against the cluster's `postgres` database, for CREATE DATABASE / DROP DATABASE, which
# cannot run inside the database being created or dropped. Refuses if the guard has not run.
ol_psql_maintenance() {
  if [[ -z "${OL_DB_URL:-}" ]]; then
    echo "FAIL: ol_psql_maintenance called before the local target guard succeeded; refusing to connect." >&2
    return 1
  fi
  psql "$OL_DB_URL" -X -v ON_ERROR_STOP=1 "$@"
}

# ---------------------------------------------------------------------------
# The persistent development database — a separate container, deliberately.
#
# `supabase db reset` drops every non-system database in the CLI's cluster, not just the project
# database. A persistent development database therefore cannot live there: one routine reset in
# the middle of the Slice 0 evidence suite would destroy it. It runs instead in its own
# container, from the same `supabase/postgres` image, on its own loopback-only port, with its own
# volume. The CLI's cluster stays purely disposable, which is exactly what the evidence suite
# expects of it.
#
# The guard below is the same shape as `ol_require_local_target` and just as fail-closed. Its
# facts are:
#
#   1. `supabase/config.toml` names this project (so the guard is still anchored to this repo);
#   2. the project is not linked, and no inherited SUPABASE_*/PG* variable can redirect anything;
#   3. the container exists, carries this repository's labels, and names *this* working tree;
#   4. the container publishes its port on a loopback address and nowhere else.
#
# Fact 4 is stricter than the CLI's own stack, which binds on all interfaces. This container is
# ours, so it binds on 127.0.0.1 only and the guard refuses it if it does not.
#
# Procedure: docs/OPERATIONS.md §4.1.

OL_DEV_CONTAINER="origenlab_dev_db"
OL_DEV_PORT="54332"
OL_DEV_DBNAME="origenlab_dev"
OL_DEV_SUPERUSER="postgres"

# ol_require_dev_database [repo root]
# On success exports OL_DEV_DB_URL (never printed), OL_DEV_DB_HOST, OL_DEV_DB_PORT.
ol_require_dev_database() {
  local root="${1:-${OL_REPO_ROOT:-$PWD}}"
  local cfg="$root/supabase/config.toml"

  ol_scrub_pg_env

  if [[ ! -f "$cfg" ]]; then
    echo "FAIL: dev guard: $cfg not found; refusing to connect." >&2
    return 1
  fi
  local cfg_project
  cfg_project="$(ol_config_value '' project_id <"$cfg")"
  if [[ "$cfg_project" != "$OL_EXPECTED_PROJECT_ID" ]]; then
    echo "FAIL: dev guard: config.toml project_id is '${cfg_project:-<unset>}', expected '$OL_EXPECTED_PROJECT_ID'; refusing to connect." >&2
    return 1
  fi
  if [[ -e "$root/supabase/.temp/project-ref" ]]; then
    echo "FAIL: dev guard: supabase/.temp/project-ref exists — this project is linked to a hosted project; refusing to connect." >&2
    return 1
  fi
  local cfg_ref
  cfg_ref="$(ol_config_value '' project_ref <"$cfg")"
  if [[ -n "$cfg_ref" ]]; then
    echo "FAIL: dev guard: config.toml declares a hosted project_ref; refusing to connect." >&2
    return 1
  fi

  if ! command -v docker >/dev/null 2>&1; then
    echo "FAIL: dev guard: docker not found; refusing to connect." >&2
    return 1
  fi

  local want_root rc=0 label_workdir label_component
  want_root="$(cd "$root" && pwd -P)"
  label_workdir="$(docker inspect "$OL_DEV_CONTAINER" --format '{{index .Config.Labels "com.origenlab.workdir"}}' 2>&1)" || rc=$?
  if (( rc != 0 )); then
    echo "FAIL: dev guard: container '$OL_DEV_CONTAINER' is not running. Start it with supabase/scripts/dev_db.sh up." >&2
    return 1
  fi
  if [[ "$label_workdir" != "$want_root" ]]; then
    echo "FAIL: dev guard: container '$OL_DEV_CONTAINER' belongs to '${label_workdir:-<unset>}', not to this working tree ('$want_root'); refusing to connect." >&2
    return 1
  fi
  label_component="$(docker inspect "$OL_DEV_CONTAINER" --format '{{index .Config.Labels "com.origenlab.component"}}' 2>/dev/null || true)"
  if [[ "$label_component" != "dev-database" ]]; then
    echo "FAIL: dev guard: container '$OL_DEV_CONTAINER' is not labelled as the development database; refusing to connect." >&2
    return 1
  fi

  # The published binding must be loopback, and it must be the only one. A container that has
  # been re-published on 0.0.0.0 is refused rather than used.
  local bindings
  bindings="$(docker inspect "$OL_DEV_CONTAINER" \
    --format "{{range \$p, \$conf := .NetworkSettings.Ports}}{{range \$conf}}{{\$p}}={{.HostIp}}:{{.HostPort}} {{end}}{{end}}" 2>/dev/null || true)"
  if [[ -z "$bindings" ]]; then
    echo "FAIL: dev guard: container '$OL_DEV_CONTAINER' publishes no port; refusing to connect." >&2
    return 1
  fi
  local b
  for b in $bindings; do
    case "${b#*=}" in
      127.0.0.1:"$OL_DEV_PORT"|'[::1]':"$OL_DEV_PORT") ;;
      *)
        echo "FAIL: dev guard: container '$OL_DEV_CONTAINER' publishes '$b', which is not loopback on port $OL_DEV_PORT; refusing to connect." >&2
        return 1
        ;;
    esac
  done

  OL_DEV_DB_HOST="127.0.0.1"
  OL_DEV_DB_PORT="$OL_DEV_PORT"
  OL_DEV_DB_URL="postgresql://${OL_DEV_SUPERUSER}:postgres@127.0.0.1:${OL_DEV_PORT}/${OL_DEV_DBNAME}"
  export OL_DEV_DB_URL OL_DEV_DB_HOST OL_DEV_DB_PORT
  printf 'dev database: role %s at 127.0.0.1:%s/%s (container %s, workdir %s)\n' \
    "$OL_DEV_SUPERUSER" "$OL_DEV_PORT" "$OL_DEV_DBNAME" "$OL_DEV_CONTAINER" "$want_root"
  return 0
}

# psql against the validated development database. Refuses if the guard has not run.
ol_psql_dev() {
  if [[ -z "${OL_DEV_DB_URL:-}" ]]; then
    echo "FAIL: ol_psql_dev called before ol_require_dev_database succeeded; refusing to connect." >&2
    return 1
  fi
  psql "$OL_DEV_DB_URL" -X -v ON_ERROR_STOP=1 "$@"
}

# psql against the development container's `postgres` database, for CREATE/DROP DATABASE.
ol_psql_dev_maintenance() {
  if [[ -z "${OL_DEV_DB_URL:-}" ]]; then
    echo "FAIL: ol_psql_dev_maintenance called before ol_require_dev_database succeeded; refusing to connect." >&2
    return 1
  fi
  psql "postgresql://${OL_DEV_SUPERUSER}:postgres@127.0.0.1:${OL_DEV_PORT}/postgres" -X -v ON_ERROR_STOP=1 "$@"
}

# ---------------------------------------------------------------------------
# Shared build steps: platform emulation and the migration chain.
#
# Both the development database and the clean-room database are built the same way — that is
# the point of having a clean room at all. The two steps below are therefore written once and
# parameterised by the psql function that reaches the target, rather than copied into each
# script where they could quietly drift apart.
#
# Every caller passes a psql wrapper that has already been through a guard, so neither helper
# resolves a target itself and neither can be pointed anywhere by an argument.

# ol_bootstrap_platform_objects_into <psql_fn> [--with-ledger]
#
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
ol_bootstrap_platform_objects_into() {
  local psql_fn="${1-}" with_ledger=0
  [[ -n "$psql_fn" ]] || { echo "FAIL: ol_bootstrap_platform_objects_into needs a psql function" >&2; return 1; }
  [[ "${2-}" == "--with-ledger" ]] && with_ledger=1

  "$psql_fn" <<'SQL' >/dev/null || return 1
create schema if not exists extensions authorization postgres;
grant usage on schema extensions to anon, authenticated, service_role;
grant usage, create on schema extensions to dashboard_user;
-- btree_gist backs the exclusion constraints on crm.affiliation,
-- crm.organization_relationship and crm.opportunity_participant. A restore of those tables
-- needs the operator class to exist before their constraints are created.
create extension if not exists btree_gist with schema extensions;
SQL

  if (( with_ledger )); then
    "$psql_fn" <<'SQL' >/dev/null || return 1
create schema if not exists supabase_migrations authorization postgres;
create table if not exists supabase_migrations.schema_migrations (
  version text not null primary key,
  statements text[],
  name text
);
SQL
  fi
  return 0
}

# ol_apply_migrations_into <psql_fn>
#
# Replays supabase/migrations/ in lexical order — which is canonical order, because every file
# name begins with its UTC timestamp and the CLI applies them the same way. One transaction per
# migration, aborting on the first error, exactly as the hosted apply does: a migration that
# fails leaves no partial state and no ledger row.
ol_apply_migrations_into() {
  local psql_fn="${1-}"
  [[ -n "$psql_fn" ]] || { echo "FAIL: ol_apply_migrations_into needs a psql function" >&2; return 1; }

  local dir="${OL_REPO_ROOT:?OL_REPO_ROOT must be set}/supabase/migrations"
  [[ -d "$dir" ]] || { echo "FAIL: $dir not found" >&2; return 1; }

  local applied=0 skipped=0 file base version name seen
  while IFS= read -r file; do
    base="$(basename "$file")"
    version="${base%%_*}"
    name="${base#*_}"; name="${name%.sql}"
    if [[ ! "$version" =~ ^[0-9]{14}$ ]]; then
      echo "FAIL: migration $base does not begin with a 14-digit version" >&2
      return 1
    fi

    seen="$("$psql_fn" -tAc \
      "select 1 from supabase_migrations.schema_migrations where version = '$version'")" \
      || { echo "FAIL: could not read the ledger" >&2; return 1; }
    if [[ -n "$seen" ]]; then
      skipped=$(( skipped + 1 ))
      continue
    fi

    echo "applying $base…" >&2
    "$psql_fn" --single-transaction -f "$file" >/dev/null \
      || { echo "FAIL: migration $base failed; nothing from it was committed and no ledger row was written" >&2; return 1; }
    "$psql_fn" -tAc \
      "insert into supabase_migrations.schema_migrations (version, name) values ('$version', '$name')" \
      >/dev/null || { echo "FAIL: migration $base applied but its ledger row could not be written" >&2; return 1; }
    applied=$(( applied + 1 ))
  done < <(find "$dir" -maxdepth 1 -name '*.sql' -type f | sort)

  echo "migrate: $applied applied, $skipped already recorded." >&2
  return 0
}

# ---------------------------------------------------------------------------
# The clean-room database — a rebuildable second database in the development container.
#
# `origenlab_dev` is persistent and, as of 2026-09-22, quarantined: a database-backed test run
# wrote seven fixture source records, seven fixture operators, nine command receipts and the
# rows they produced into it, and its migration ledger no longer describes its own schema. It
# still holds twenty real staged Gmail records, so it is neither trustworthy nor disposable.
#
# The clean room is the answer to both halves of that. `origenlab_clean` is rebuilt from
# nothing but supabase/migrations/ and the reproducible historical load, so what is in it is
# exactly what the reviewed inputs produce — and because it is rebuilt rather than repaired,
# `origenlab_dev` never has to be written to, dropped or restored over.
#
# THE NAME IS A LITERAL AND IS NEVER TAKEN FROM AN ARGUMENT OR THE ENVIRONMENT. That is the
# whole safety property: the clean-room build drops a database, and a build that could be told
# which one is a build that could be told to drop `origenlab_dev`. The guard below refuses
# outright if the constant is anything other than `origenlab_clean`, and refuses the name
# `origenlab_dev` by name before it does anything else.

OL_CLEAN_DBNAME="origenlab_clean"

# The name this repository will never let the clean-room tooling open. Listed explicitly rather
# than inferred, so the refusal is greppable and testable.
OL_CLEAN_FORBIDDEN_DBNAME="origenlab_dev"

# ol_require_cleanroom_database [repo root]
# Re-proves every fact `ol_require_dev_database` proves — this project, not linked, the
# container belongs to this working tree and publishes on loopback only — and then pins the
# database name to the literal `origenlab_clean`.
#
# On success exports OL_CLEAN_DB_URL (never printed) and OL_CLEAN_DB_PORT, and UNSETS
# OL_DEV_DB_URL, so that inside a clean-room script `ol_psql_dev` cannot connect at all.
ol_require_cleanroom_database() {
  local root="${1:-${OL_REPO_ROOT:-$PWD}}"

  # Fail before anything else if the constant has been tampered with. A clean-room build drops
  # a database; the one thing that must never be in doubt is which one.
  if [[ "$OL_CLEAN_DBNAME" != "origenlab_clean" ]]; then
    echo "FAIL: clean-room guard: the database name constant is '${OL_CLEAN_DBNAME}', not 'origenlab_clean'; refusing to connect." >&2
    return 1
  fi
  if [[ "$OL_CLEAN_DBNAME" == "$OL_CLEAN_FORBIDDEN_DBNAME" ]]; then
    echo "FAIL: clean-room guard: the clean-room name resolved to '$OL_CLEAN_FORBIDDEN_DBNAME', which this tooling never opens; refusing to connect." >&2
    return 1
  fi

  ol_require_dev_database "$root" >/dev/null || return 1

  # The development database's DSN is deliberately discarded. Nothing downstream of this guard
  # has a working handle on `origenlab_dev`.
  unset OL_DEV_DB_URL

  OL_CLEAN_DB_PORT="$OL_DEV_PORT"
  OL_CLEAN_DB_URL="postgresql://${OL_DEV_SUPERUSER}:postgres@127.0.0.1:${OL_DEV_PORT}/${OL_CLEAN_DBNAME}"
  export OL_CLEAN_DB_URL OL_CLEAN_DB_PORT
  printf 'clean-room database: role %s at 127.0.0.1:%s/%s (container %s)\n' \
    "$OL_DEV_SUPERUSER" "$OL_CLEAN_DB_PORT" "$OL_CLEAN_DBNAME" "$OL_DEV_CONTAINER"
  return 0
}

# psql against the validated clean-room database. Refuses if the guard has not run.
ol_psql_clean() {
  if [[ -z "${OL_CLEAN_DB_URL:-}" ]]; then
    echo "FAIL: ol_psql_clean called before ol_require_cleanroom_database succeeded; refusing to connect." >&2
    return 1
  fi
  psql "$OL_CLEAN_DB_URL" -X -v ON_ERROR_STOP=1 "$@"
}

# psql against the container's `postgres` database, for CREATE/DROP DATABASE only.
ol_psql_clean_maintenance() {
  if [[ -z "${OL_CLEAN_DB_URL:-}" ]]; then
    echo "FAIL: ol_psql_clean_maintenance called before ol_require_cleanroom_database succeeded; refusing to connect." >&2
    return 1
  fi
  psql "postgresql://${OL_DEV_SUPERUSER}:postgres@127.0.0.1:${OL_CLEAN_DB_PORT}/postgres" \
    -X -v ON_ERROR_STOP=1 "$@"
}
