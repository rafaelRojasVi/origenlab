#!/usr/bin/env bash
# A throwaway PostgreSQL cluster for the API's database-backed tests.
#
# The V2 command and read tests (tests/v2_command_harness.py) skip unless two connections exist:
# ORIGENLAB_V2_TEST_DSN, a maintenance login that may create a database and SET ROLE
# origenlab_owner, and ORIGENLAB_V2_API_TEST_DSN, the real origenlab_api login. This script
# starts a fresh `supabase/postgres` container, applies supabase/roles.sql as `postgres` (a
# non-superuser, the way the CLI and CI do), gives `postgres` and `origenlab_api` random
# passwords that live only as long as the container, and writes both DSNs to an env file.
#
# The cluster holds no data: its `postgres` database carries the migrated schema, empty, and the
# suite creates and drops its own origenlab_test_<hex> databases beside it.
# Passwords are never printed; in GitHub Actions they are masked.
#
# Usage:
#   apps/api/scripts/disposable_test_cluster.sh up <env-file>   # prints the container name, also OL_TEST_CLUSTER in <env-file>
#   apps/api/scripts/disposable_test_cluster.sh down <container>
#
#   set -a; . <env-file>; set +a; ./scripts/validate.sh

set -euo pipefail

IMAGE="${ORIGENLAB_TEST_PG_IMAGE:-public.ecr.aws/supabase/postgres:17.6.1.165}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd -P)"

die() { echo "FAIL: $*" >&2; exit 1; }

secret() { openssl rand -hex 24; }

# The public registry rate-limits anonymous pulls ("toomanyrequests"), and CI's other jobs pull
# from it at the same moment. Retry it, then fall back to the same image on Docker Hub, pinned by
# the digest both registries serve, and tag it locally under $IMAGE.
IMAGE_DIGEST="${ORIGENLAB_TEST_PG_DIGEST:-sha256:28f0e16a019e648089fc1a6d333549a55548f6019c15ae4bd7cd58b989027518}"
MIRROR="docker.io/supabase/postgres@$IMAGE_DIGEST"

pull_image() {
  docker image inspect "$IMAGE" >/dev/null 2>&1 && return 0
  local attempt delay=15
  for attempt in 1 2 3; do
    docker pull -q "$IMAGE" >/dev/null && return 0
    [[ $attempt -lt 3 ]] || break
    echo "pull of $IMAGE failed (attempt $attempt); retrying in ${delay}s" >&2
    sleep "$delay"; delay=$((delay * 2))
  done
  echo "falling back to $MIRROR" >&2
  for attempt in 1 2 3; do
    if docker pull -q "$MIRROR" >/dev/null; then
      docker tag "$MIRROR" "$IMAGE"
      return 0
    fi
    sleep 15
  done
  die "could not pull $IMAGE or $MIRROR"
}

up() {
  local env_file="$1" name pg_pw api_pw port
  [[ -n "$env_file" ]] || die "usage: up <env-file>"
  name="origenlab_apitest_$(openssl rand -hex 4)"
  pg_pw="$(secret)"
  api_pw="$(secret)"
  # Workflow commands are read from the step's own output, so a caller must never capture this
  # script's stdout in Actions; the container name is also written to the env file.
  if [[ -n "${GITHUB_ACTIONS:-}" ]]; then
    echo "::add-mask::$pg_pw"
    echo "::add-mask::$api_pw"
  fi

  pull_image

  trap 'docker rm -f "$name" >/dev/null 2>&1 || true' ERR
  docker run -d --name "$name" --publish 127.0.0.1::5432 \
    -e POSTGRES_PASSWORD="$pg_pw" "$IMAGE" >/dev/null

  # The image restarts PostgreSQL once during initialisation: wait for two successes in a row.
  local ok=0
  for _ in $(seq 1 90); do
    if docker exec "$name" psql -U postgres -d postgres -Atc 'select 1' >/dev/null 2>&1; then
      ok=$((ok + 1)); [[ $ok -ge 2 ]] && break
    else
      ok=0
    fi
    sleep 2
  done
  [[ $ok -ge 2 ]] || { docker logs "$name" >&2 || true; die "cluster $name never became ready"; }

  docker exec -i "$name" psql -U postgres -d postgres -v ON_ERROR_STOP=1 -q \
    < "$REPO_ROOT/supabase/roles.sql" >/dev/null 2>&1
  # A login's password is a superuser's statement in this image; the roles themselves came
  # from roles.sql as postgres, above.
  docker exec -i "$name" psql -U supabase_admin -d postgres -v ON_ERROR_STOP=1 -q >/dev/null <<SQL
alter role postgres password '$pg_pw';
alter role origenlab_api password '$api_pw';
SQL

  # The read-boundary tests (test_v2_read_boundary.py) read the maintenance connection's own
  # database rather than minting one, as they do against the CLI stack; give it the chain.
  local migration
  for migration in "$REPO_ROOT"/supabase/migrations/*.sql; do
    docker exec -i "$name" psql -U postgres -d postgres -v ON_ERROR_STOP=1 -q \
      < "$migration" >/dev/null || die "migration $(basename "$migration") failed"
  done

  port="$(docker port "$name" 5432/tcp | head -n 1 | rpartition_port)"
  umask 077
  cat > "$env_file" <<ENV
ORIGENLAB_V2_TEST_DSN=postgresql://postgres:$pg_pw@127.0.0.1:$port/postgres
ORIGENLAB_V2_API_TEST_DSN=postgresql://origenlab_api:$api_pw@127.0.0.1:$port/postgres
OL_TEST_CLUSTER=$name
ENV
  trap - ERR
  echo "$name"
}

rpartition_port() { sed -E 's/.*:([0-9]+)$/\1/'; }

down() {
  local name="$1"
  [[ "$name" =~ ^origenlab_apitest_[0-9a-f]{8}$ ]] || die "refusing to remove '$name': not a disposable test cluster"
  docker rm -f "$name" >/dev/null
}

case "${1:-}" in
  up) up "${2:-}" ;;
  down) down "${2:-}" ;;
  *) die "usage: $0 up <env-file> | down <container>" ;;
esac
