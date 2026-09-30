#!/usr/bin/env bash
# OrigenLab V2 — the two clean-room verification contracts, written once.
#
# Sourced by supabase/scripts/cleanroom_db.sh (which runs them against `origenlab_clean` behind
# the clean-room guard) and by supabase/scripts/cleanroom_verify_tests.sh (which runs them
# against disposable databases to prove they fail on what they claim to fail on). Neither
# function opens a connection of its own: each is handed a psql wrapper that has already been
# through a guard, and pipes that wrapper's read-only output into compare.py.
#
#   ol_verify_fresh_rebuild <psql_fn>   verify.sql        vs expected_counts.json
#   ol_verify_data_bearing  <psql_fn>   data_bearing.sql  vs expected_data_bearing.json
#
# FRESH-REBUILD is exact row counts: true immediately after `build`, and only then. It is what
# `build` runs last, and what `verify --fresh-rebuild` re-runs.
#
# DATA-BEARING is the contract of a clean room that has since carried operator decisions —
# the ledger equals the files on disk, the inventory, the grant boundary, the sign-in privilege
# boundary, the send flags, no fixture residue. It counts no business row, so it does not need
# zero opportunities or one operator to pass, and it is what `verify` checks by default.
#
# Requires OL_REPO_ROOT. Procedure: docs/OPERATIONS.md §4.1 (“Verify it”).

OL_CLEANROOM_DIR="${OL_REPO_ROOT:?OL_REPO_ROOT must be set}/supabase/cleanroom"

# The versions in supabase/migrations/, sorted, comma-separated — the expectation the ledger is
# measured against. Computed from the files, never from a database.
ol_chain_versions_on_disk() {
  find "$OL_REPO_ROOT/supabase/migrations" -maxdepth 1 -name '*.sql' -type f -printf '%f\n' \
    | sort | cut -d_ -f1 | paste -sd, -
}

ol_verify_fresh_rebuild() {
  local psql_fn="${1-}"
  [[ -n "$psql_fn" ]] || { echo "FAIL: ol_verify_fresh_rebuild needs a psql function" >&2; return 1; }
  "$psql_fn" -q -f "$OL_CLEANROOM_DIR/verify.sql" \
    | python3 "$OL_CLEANROOM_DIR/compare.py" "$OL_CLEANROOM_DIR/expected_counts.json"
}

ol_verify_data_bearing() {
  local psql_fn="${1-}"
  [[ -n "$psql_fn" ]] || { echo "FAIL: ol_verify_data_bearing needs a psql function" >&2; return 1; }
  local chain
  chain="$(ol_chain_versions_on_disk)"
  [[ -n "$chain" ]] || { echo "FAIL: no migration found under supabase/migrations/" >&2; return 1; }
  "$psql_fn" -q -v chain_on_disk="$chain" -f "$OL_CLEANROOM_DIR/data_bearing.sql" \
    | python3 "$OL_CLEANROOM_DIR/compare.py" "$OL_CLEANROOM_DIR/expected_data_bearing.json"
}
