#!/usr/bin/env bash
# Read-only guardrail: fail if tracked git content matches public-repo risk patterns.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "FAIL: not inside a git repository" >&2
  exit 1
fi

FAILURES=0

fail() {
  echo "FAIL: $1" >&2
  FAILURES=$((FAILURES + 1))
}

pass() {
  echo "OK: $1"
}

is_allowed_reports_out_file() {
  case "$1" in
    apps/email-pipeline/reports/out/README.md | apps/email-pipeline/reports/out/.gitkeep)
      return 0
      ;;
  esac
  return 1
}

section() {
  printf "\n== %s ==\n" "$1"
}

section "tracked secret / credential patterns"
while IFS= read -r path; do
  [[ -z "$path" ]] && continue
  base="${path##*/}"

  case "$base" in
    .env)
      fail "tracked env file: $path"
      ;;
    .env.example)
      ;;
    .env.*)
      fail "tracked env variant: $path"
      ;;
  esac

  case "$path" in
    */id_rsa | id_rsa)
      fail "tracked private key path: $path"
      ;;
  esac

  case "$path" in
    *.sqlite | *.sqlite3 | *.db | *.mbox | *.pst | *.jsonl | *.pem | *.p12)
      fail "tracked sensitive artifact: $path"
      ;;
  esac
done < <(git ls-files)

section "tracked operational report paths"
while IFS= read -r path; do
  [[ -z "$path" ]] && continue

  case "$path" in
    apps/email-pipeline/reports/out/*)
      if is_allowed_reports_out_file "$path"; then
        continue
      fi
      fail "tracked email-pipeline reports/out artifact: $path"
      ;;
    apps/email-pipeline/reports/in/*)
      fail "tracked email-pipeline reports/in input: $path"
      ;;
    reports/in/*)
      fail "tracked monorepo reports/in input: $path"
      ;;
    reports/out/*)
      fail "tracked monorepo reports/out artifact: $path"
      ;;
  esac
done < <(git ls-files)

section "tracked Slice 0 audit local state"
# The hosted target, the operator attestation and every audit report are local, git-ignored files
# at three approved paths (docs/OPERATIONS.md §4.2). None of them may ever be tracked: the target
# file names a hosted project, the attestation binds to one, and a report is generated evidence.
while IFS= read -r path; do
  [[ -z "$path" ]] && continue
  case "$path" in
    supabase/.audit/*)
      fail "tracked Slice 0 audit local state: $path"
      ;;
  esac
  case "${path##*/}" in
    hosted_target.env)
      fail "tracked hosted audit target file: $path"
      ;;
  esac
  case "$path" in
    */attestation.json | attestation.json)
      fail "tracked operator attestation: $path"
      ;;
  esac
done < <(git ls-files)

section "tracked hosted project identifiers"
# A hosted Supabase project reference must never enter tracked content. The direct-connection host
# name is the shape it takes: db.<twenty lowercase letters>.supabase.co. The audit's own fixtures
# and tests use a deliberately fake reference, so a match here is either a real leak or a fixture
# that should be using the fake one -- both worth stopping on.
HOSTED_HOST_MATCHES="$(
  git grep -InE 'db\.[a-z]{20}\.supabase\.co' -- \
    ':!supabase/audit/tests/*' ':!supabase/audit/fixtures/*' ':!supabase/scripts/audit_failure_tests.sh' \
    ':!supabase/scripts/hosted_bootstrap_failure_tests.sh' \
    ':!supabase/audit/olaudit/*' ':!docs/OPERATIONS.md' 2>/dev/null || true
)"
if [[ -n "$HOSTED_HOST_MATCHES" ]]; then
  fail "tracked hosted Supabase host name outside the audit's own fixtures and documentation"
  printf '%s\n' "$HOSTED_HOST_MATCHES" >&2
else
  pass "no tracked hosted Supabase host name"
fi

section "no credential in a role bootstrap"
# supabase/roles.sql and supabase/hosted_roles.sql create LOGIN roles. Neither may ever carry a
# password: local credentials are throw-away ones set by verify_direct_logins.sh and cleared
# fail-closed, and the hosted migrator credential is assigned out of band with a hidden secret
# input (docs/OPERATIONS.md §4.3). supabase/audit/olaudit/bootstrap.py enforces this statically for
# the hosted file; this is the independent check over tracked bytes, and it covers both files.
BOOTSTRAP_CREDENTIALS=""
for bootstrap_file in supabase/roles.sql supabase/hosted_roles.sql; do
  [[ -f "$bootstrap_file" ]] || continue
  # Comments are stripped first: both files legitimately explain in prose why no password appears.
  match="$(sed 's/--.*$//' "$bootstrap_file" | grep -InEi 'password|encrypted[[:space:]]+|valid[[:space:]]+until' || true)"
  if [[ -n "$match" ]]; then
    BOOTSTRAP_CREDENTIALS+="$bootstrap_file: $match"$'\n'
  fi
done
if [[ -n "$BOOTSTRAP_CREDENTIALS" ]]; then
  fail "a role bootstrap file assigns or mentions a credential outside a comment"
  printf '%s\n' "$BOOTSTRAP_CREDENTIALS" >&2
else
  pass "no credential in supabase/roles.sql or supabase/hosted_roles.sql"
fi


section "tracked client collateral"
while IFS= read -r path; do
  [[ -z "$path" ]] && continue
  case "$path" in
    docs/client/*)
      fail "tracked client collateral: $path"
      ;;
  esac
done < <(git ls-files)

section "workflow permissions (contents: read)"
WORKFLOW_FILES=(
  .github/workflows/email-pipeline.yml
  .github/workflows/api.yml
  .github/workflows/dashboard.yml
  .github/workflows/secret-scan.yml
  .github/workflows/supabase.yml
)
for workflow in "${WORKFLOW_FILES[@]}"; do
  if [[ ! -f "$workflow" ]]; then
    fail "missing workflow file: $workflow"
    continue
  fi
  if ! grep -q '^permissions:' "$workflow"; then
    fail "workflow missing permissions block: $workflow"
    continue
  fi
  if ! grep -q 'contents:[[:space:]]*read' "$workflow"; then
    fail "workflow missing 'contents: read': $workflow"
    continue
  fi
  pass "workflow permissions: $workflow"
done

section "summary"
if [[ "$FAILURES" -gt 0 ]]; then
  echo
  echo "Public repo hygiene check failed with $FAILURES issue(s)." >&2
  echo "See docs/SECURITY_PUBLIC_REPO.md" >&2
  exit 1
fi

echo "Public repo hygiene check passed."
