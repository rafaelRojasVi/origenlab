#!/usr/bin/env bash
# OrigenLab V2 — hosted role bootstrap, dry run.
#
#   supabase/scripts/hosted_role_bootstrap.sh --environment staging --dry-run
#
# Prints the reviewed contents of supabase/hosted_roles.sql to stdout, and the plan summary to
# stderr, after proving statically that the file is nothing but role management over the four
# OrigenLab roles.
#
# THIS TOOL OPENS NO DATABASE CONNECTION IN ANY MODE. There is no apply mode, no host argument, no
# connection string, no target file and no credential input. Applying the bootstrap is a separate,
# deliberate operator action; assigning the origenlab_migrator password is another one, performed
# with a hidden secret input. Procedure: docs/OPERATIONS.md §4.3.
#
# The environment classification is required and is validated against a closed, non-secret
# allowlist. It never reaches the emitted SQL, which is byte-identical whichever environment it is
# reviewed for.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

command -v python3 >/dev/null 2>&1 || { echo "FAIL: python3 is required" >&2; exit 2; }

exec python3 "$ROOT/supabase/audit/run_bootstrap.py" "$@"
