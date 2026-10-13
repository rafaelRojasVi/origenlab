#!/usr/bin/env bash
# Disposable test databases for the Python suites, the same locally and in CI.
#
#   scripts/test_db.sh up <env-file> [--v2] [--v1]   # no flag: both
#   scripts/test_db.sh down <env-file>
#
# --v2  the Supabase-image cluster from apps/api/scripts/disposable_test_cluster.sh (V2 command,
#       read and worker tests).
# --v1  a postgres:16-alpine with two databases at the email-pipeline Alembic head:
#       origenlab_v1_api_test (ORIGENLAB_TEST_POSTGRES_URL, api's commercial.* tests) and
#       origenlab_v1_pipeline_test (OL_PIPELINE_PG_TEST_URL, email-pipeline's transactional tests).
#
# Containers carry a random suffix, so worktrees never collide; `down` removes only names this
# script creates. Nothing here can reach the local Supabase stack or a hosted database.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
CLUSTER="${OL_DISPOSABLE_CLUSTER_SCRIPT:-$ROOT/apps/api/scripts/disposable_test_cluster.sh}"
V1_IMAGE="${ORIGENLAB_TEST_PG16_IMAGE:-postgres:16-alpine}"
die() { echo "FAIL: $*" >&2; exit 1; }

# A failed `up` removes what it had started, so a retry never meets a half-built env file.
undo_failed_up() {
  local rc=$?
  if [[ $rc -ne 0 && -n "${UP_ENV_FILE:-}" ]]; then
    echo "FAIL: test databases did not start; removing what was started." >&2
    down "$UP_ENV_FILE" >/dev/null 2>&1 || true
  fi
}

up() {
  local env_file="$1"; shift
  local v1=0 v2=0
  [[ $# -eq 0 ]] && { v1=1; v2=1; }
  for flag in "$@"; do
    case "$flag" in --v1) v1=1 ;; --v2) v2=1 ;; *) die "unknown flag $flag" ;; esac
  done
  [[ -e "$env_file" ]] && die "$env_file exists; run 'make db-down' first."
  : > "$env_file"
  chmod 600 "$env_file"
  UP_ENV_FILE="$env_file"
  trap undo_failed_up EXIT

  if [[ $v2 == 1 ]]; then
    "$CLUSTER" up "$env_file"
  fi
  if [[ $v1 == 1 ]]; then
    local name password port
    name="origenlab_v1test_$(openssl rand -hex 4)"
    password="$(openssl rand -hex 24)"
    [[ -n "${GITHUB_ACTIONS:-}" ]] && echo "::add-mask::$password"
    docker run -d --rm --name "$name" -e POSTGRES_PASSWORD="$password" \
      -p 127.0.0.1::5432 "$V1_IMAGE" >/dev/null
    echo "OL_TEST_V1_CLUSTER=$name" >> "$env_file"
    port="$(docker port "$name" 5432/tcp | head -1 | sed 's/.*://')"
    # Over TCP: the image's first, init-only server answers on the socket and then restarts.
    local ready=0
    for _ in $(seq 1 60); do
      if docker exec "$name" pg_isready -h 127.0.0.1 -U postgres -q; then ready=1; break; fi
      sleep 1
    done
    [[ $ready == 1 ]] || die "$name did not accept connections within 60 s"
    for db in origenlab_v1_api_test origenlab_v1_pipeline_test; do
      docker exec "$name" createdb -h 127.0.0.1 -U postgres "$db"
      (cd "$ROOT/apps/email-pipeline" && \
        ALEMBIC_DATABASE_URL="postgresql+psycopg://postgres:$password@127.0.0.1:$port/$db" \
        uv run --frozen alembic -c alembic.ini upgrade head >/dev/null)
    done
    echo "ORIGENLAB_TEST_POSTGRES_URL=postgresql://postgres:$password@127.0.0.1:$port/origenlab_v1_api_test" >> "$env_file"
    echo "OL_PIPELINE_PG_TEST_URL=postgresql://postgres:$password@127.0.0.1:$port/origenlab_v1_pipeline_test" >> "$env_file"
  fi
  trap - EXIT
  echo "test databases up: $env_file"
}

down() {
  local env_file="$1" v2 v1
  [[ -f "$env_file" ]] || { echo "no $env_file; nothing to stop."; return 0; }
  v2="$(grep -E '^OL_TEST_CLUSTER=' "$env_file" | cut -d= -f2- || true)"
  v1="$(grep -E '^OL_TEST_V1_CLUSTER=' "$env_file" | cut -d= -f2- || true)"
  [[ -z "$v2" || "$v2" =~ ^origenlab_apitest_[0-9a-f]{8}$ ]] || die "refusing to remove $v2"
  [[ -z "$v1" || "$v1" =~ ^origenlab_v1test_[0-9a-f]{8}$ ]] || die "refusing to remove $v1"
  [[ -n "$v2" ]] && "$CLUSTER" down "$v2"
  [[ -n "$v1" ]] && docker rm -f "$v1" >/dev/null
  rm -f "$env_file"
  echo "test databases down."
}

case "${1:-}" in
  up) shift; up "${1:?env file}" "${@:2}" ;;
  down) shift; down "${1:?env file}" ;;
  *) die "usage: test_db.sh up|down <env-file> [--v1] [--v2]" ;;
esac
