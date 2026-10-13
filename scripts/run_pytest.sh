#!/usr/bin/env bash
# Run one Python app's pytest suite the same way locally and in CI: parallel (pytest-xdist, one
# file per worker), against the disposable databases scripts/test_db.sh started, and failing if
# a database test skipped — a green run that skipped them proves nothing.
#
#   scripts/run_pytest.sh <api|worker|email-pipeline> [pytest args...]
#
#   OL_TEST_DB_ENV      env file from `scripts/test_db.sh up` (default: <repo>/.test-db.env)
#   SKIP_DB=1           run without databases; the skipped count is printed, not hidden
#   OL_DB_SKIP_PATTERN  ERE naming a database skip (default: V2 DSNs and the V1 Postgres URL)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
app="${1:?usage: run_pytest.sh <app> [pytest args...]}"
shift
ENV_FILE="${OL_TEST_DB_ENV:-$ROOT/.test-db.env}"
# A skip naming only the database variable. "…POSTGRES_URL and ORIGENLAB_TEST_SQLITE_PATH" skips
# also need a real SQLite archive, which no disposable setup provides, so they do not count.
PATTERN="${OL_DB_SKIP_PATTERN:-ORIGENLAB_V2_[A-Z_]*TEST_DSN|ORIGENLAB_TEST_POSTGRES_URL to}"

# uv warns when an outer venv is active, and the warning carries a /home path into CLI output
# that leakage tests assert is absent.
unset VIRTUAL_ENV

if [[ "${SKIP_DB:-0}" != 1 ]]; then
  if [[ ! -f "$ENV_FILE" ]]; then
    echo "FAIL: no test databases ($ENV_FILE). Run 'make db-up' (or SKIP_DB=1 to skip them)." >&2
    exit 1
  fi
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
  for name in "${OL_TEST_CLUSTER:-}" "${OL_TEST_V1_CLUSTER:-}"; do
    if [[ -n "$name" ]] && ! docker container inspect "$name" >/dev/null 2>&1; then
      echo "FAIL: test database container $name is gone. Run 'make db-down && make db-up'." >&2
      exit 1
    fi
  done
fi

cd "$ROOT/apps/$app"
log="$(mktemp)"
trap 'rm -f "$log"' EXIT

# Two phases. Everything runs in parallel except tests marked `serial_db`: they use database
# state every worker shares (cluster-wide roles, or one fixed database), which no per-worker
# clone can contain, so they run alone afterwards. Exit 5 is "no tests selected", which a phase may legitimately be.
export PYTHONPATH="$ROOT/scripts/pytest_plugins${PYTHONPATH:+:$PYTHONPATH}"
common=(-p pg_per_worker -q -rfEs --durations=10)
set +e
uv run --frozen pytest -n auto --dist loadfile -m "not serial_db" "${common[@]}" "$@" \
  | tee "$log"
rc_parallel=${PIPESTATUS[0]}
uv run --frozen pytest -n 0 -m serial_db "${common[@]}" "$@" | tee -a "$log"
rc_alone=${PIPESTATUS[0]}
set -e
[[ $rc_parallel == 5 ]] && rc_parallel=0
[[ $rc_alone == 5 ]] && rc_alone=0
rc=$(( rc_parallel != 0 ? rc_parallel : rc_alone ))

db_skips="$(grep -E "^SKIPPED .*($PATTERN)" "$log" \
  | awk -F'[][]' '{n += $2} END {print n + 0}' || true)"
db_skips="${db_skips:-0}"
if [[ "$db_skips" != 0 ]]; then
  if [[ "${SKIP_DB:-0}" == 1 ]]; then
    echo "note: $db_skips database tests skipped (SKIP_DB=1)." >&2
  else
    echo "FAIL: $db_skips database tests skipped although test databases are configured." >&2
    exit 1
  fi
fi
exit "$rc"
