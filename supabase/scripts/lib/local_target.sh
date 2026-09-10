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
