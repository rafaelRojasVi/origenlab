#!/usr/bin/env bash
# OrigenLab V2 — Slice 0 audit: a read-only auditor for the foundation, local or hosted.
#
#   supabase/scripts/slice0_audit.sh --mode local
#   supabase/scripts/slice0_audit.sh --mode hosted --simulate
#   supabase/scripts/slice0_audit.sh --mode hosted --authorize-hosted-connection
#
# The audit has no write mode. It issues no INSERT, UPDATE, DELETE, DDL, sequence change or any
# other intentional mutation against any database, and it cannot regenerate its own baselines.
#
# Local mode resolves its target through supabase/scripts/lib/local_target.sh and cannot reach a
# remote host. Hosted mode reads a reviewed target file at one approved path, refuses link state,
# requires a clean tracked working tree and an explicit authorisation flag, and connects over
# verify-full TLS to an address it resolved and pinned itself.
#
# Design and procedure: docs/OPERATIONS.md §4.2. Obligations: docs/MIGRATION.md §5.2.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

command -v python3 >/dev/null 2>&1 || { echo "FAIL: python3 is required" >&2; exit 2; }

exec python3 "$ROOT/supabase/audit/run_audit.py" "$@"
