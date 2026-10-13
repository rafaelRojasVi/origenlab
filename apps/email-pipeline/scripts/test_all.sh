#!/usr/bin/env bash
# The whole email-pipeline suite, the way CI runs it: first the three transactional Postgres
# test files against the V1 test database (scripts/test_db.sh), then everything else with
# ORIGENLAB_POSTGRES_TEST_URL unset, so optional PG integrations that need a preloaded archive
# stay skip-gated, and the V2 proofs against the disposable V2 cluster.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(cd ../.. && pwd -P)"
ENV_FILE="${OL_TEST_DB_ENV:-$ROOT/.test-db.env}"

./scripts/sync_test_env.sh -q

if [[ "${SKIP_DB:-0}" != 1 ]]; then
  [[ -f "$ENV_FILE" ]] || { echo "FAIL: no test databases ($ENV_FILE). Run 'make db-up'." >&2; exit 1; }
  pg_url="$(grep -E '^OL_PIPELINE_PG_TEST_URL=' "$ENV_FILE" | cut -d= -f2-)"
  ORIGENLAB_POSTGRES_TEST_URL="$pg_url" OL_DB_SKIP_PATTERN='ORIGENLAB_POSTGRES_TEST_URL' \
    "$ROOT/scripts/run_pytest.sh" email-pipeline \
      tests/test_postgres_transactional_publication.py \
      tests/test_sqlite_outbound_sidecars_to_postgres_migrate.py \
      tests/test_sqlite_mart_core_to_postgres_migrate.py
fi
# The default skip guard: a V2 proof or an ORIGENLAB_TEST_POSTGRES_URL test that skips fails the
# run. Only ORIGENLAB_POSTGRES_TEST_URL skips are expected here (that URL is the phase above).
"$ROOT/scripts/run_pytest.sh" email-pipeline "$@"
