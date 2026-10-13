# One entry point for every check, the same locally and in CI. Logic lives in scripts/.
#   make setup      install every app's dependencies and test groups
#   make db-up      start the disposable test databases (.test-db.env); make db-down stops them
#   make lint       ruff, gitleaks and file checks on every file (pre-commit)
#   make check      lint, then only what your change needs (vs origin/main); SKIP_DB=1 skips DB tests
#   make check-all  everything
SHELL := bash
.SHELLFLAGS := -euo pipefail -c
.PHONY: setup lint check check-all db-up db-down test-api test-worker test-email-pipeline \
        test-dashboard test-proxy test-web test-supabase test-repo

setup:
	cd apps/api && uv sync --group dev --frozen
	cd apps/worker && uv sync --group dev --frozen
	cd apps/email-pipeline && ./scripts/sync_test_env.sh
	cd apps/dashboard && npm ci
	cd apps/dashboard-proxy && npm ci
	cd apps/web && npm ci
	uv tool install pre-commit
	pre-commit install

lint:
	scripts/lint.sh

check:
	scripts/check.sh

check-all:
	scripts/check.sh --all

db-up:
	scripts/test_db.sh up .test-db.env

db-down:
	scripts/test_db.sh down .test-db.env

test-api:
	apps/api/scripts/validate.sh

test-worker:
	apps/worker/scripts/validate.sh

test-email-pipeline:
	apps/email-pipeline/scripts/test_all.sh

test-dashboard:
	cd apps/dashboard && npm run validate

test-proxy:
	cd apps/dashboard-proxy && npm run validate && npm run typecheck

test-web:
	cd apps/web && npm run check && npm run validate:catalog && npm run build

# The database-free half of the Supabase CI job. The other half resets a local Supabase stack
# (`supabase db reset`), which would wipe a developer's local data, so it runs only in CI. So does
# local_db_failure_tests.sh: where a development container exists it exercises that container,
# which belongs to one checkout, so its result depends on the machine.
test-supabase:
	bash -n supabase/scripts/*.sh supabase/scripts/lib/*.sh
	scripts/security/check-public-repo-hygiene.sh
	python3 -m unittest discover -s supabase/audit/tests -t supabase/audit
	supabase/scripts/hosted_role_bootstrap.sh --environment staging --dry-run | cmp supabase/hosted_roles.sql -
	supabase/scripts/hosted_bootstrap_failure_tests.sh
	supabase/scripts/start_local_db_retry_tests.sh
	supabase/scripts/cleanroom_verify_tests.sh --static

test-repo:
	python3 -m unittest discover -s scripts/tests -t .
