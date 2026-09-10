#!/usr/bin/env bash
# OrigenLab V2 — print the validated local database URL, and nothing else, on standard output.
#
# A one-way bridge between the Slice 0 audit engine (Python) and the existing local-target guard
# (bash). The guard in lib/local_target.sh is the only thing in this repository allowed to decide
# what "local" means, and it is not duplicated here: this file sources it unchanged, runs it, and
# forwards the URL it produced.
#
# Everything the guard prints on success -- the project, role, host, port and the four facts it
# checked -- is sent to standard error, so standard output carries exactly the URL and the caller
# cannot accidentally parse a diagnostic as a target. On failure nothing is printed on standard
# output at all and the exit status is non-zero, so a caller that ignores the status still has no
# target to connect to.
#
# The URL is a local, throw-away credential (supabase/roles.sql creates the LOGIN roles without a
# password; the local `postgres` password is the CLI's fixed development one). It is still treated
# as a secret by the caller: it is held in memory, never logged, and never written to a report.
#
# Procedure: docs/OPERATIONS.md §4.2.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
# shellcheck source=local_target.sh
source "$ROOT/supabase/scripts/lib/local_target.sh"

ol_require_local_target "$ROOT" 1>&2 || exit 1

printf '%s' "$OL_DB_URL"
