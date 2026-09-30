#!/usr/bin/env bash
# OrigenLab V2 — regression tests for the two clean-room verification contracts.
#
# The fresh-rebuild contract (verify.sql / expected_counts.json) went stale twice without anyone
# noticing: at 21 migrations when the 22nd landed, and at 25 when fifteen more did, so by
# 2026-09-30 it failed 21 of 41 probes against `origenlab_clean` — two because the chain had
# moved, nineteen because operators had taken decisions in it. Only the first two were drift.
# This file makes both mistakes impossible to repeat quietly:
#
#   --static  (no database; runs in CI before the stack starts, and locally in seconds)
#     S1  the fresh fixture's chain probes equal supabase/migrations/ on disk;
#     S2  the two expectation files are disjoint in what they assert — no data-bearing probe
#         counts a business row, and every probe in each is emitted by its own SQL;
#     S3  compare.py fails the data-bearing contract on a bent invariant, a missing probe and
#         an undeclared probe, and names each;
#     S4  the dev scratch guard refuses every name but origenlab_test_<8 hex>, and its psql
#         wrapper refuses before the guard.
#
#   --chain  (a disposable database carrying the full chain and no business data)
#     C1  the data-bearing contract PASSES on it — it needs no rows to hold;
#     C2  the fresh-rebuild contract FAILS on it, naming the historical load it lacks — the
#         fresh contract was not weakened;
#     C3–C9 fault injection, one clone per fault: a ledger row deleted, a phantom ledger row,
#         EXECUTE on begin_pin_attempt revoked, SELECT on pin_hash granted, UPDATE on
#         platform.operator granted, RLS disabled on auth_session, a definer's search_path
#         unpinned — each fails closed and names the probe.
#
#   --restored  (only where the development container is this working tree's)
#     R1  origenlab_clean is dumped with the container's own pg_dump (read-only) and restored
#         into a scratch origenlab_test_<8 hex> in the same container; the data-bearing
#         contract PASSES on the copy;
#     R2  the fresh-rebuild contract FAILS on the copy, naming decision-driven counts;
#     R3  origenlab_clean's write counters and ledger are identical before and after.
#
# Default (no flag): --static, then --chain and --restored on whichever cluster this working
# tree can prove is its own. Where the development container belongs to another tree, the
# restored part reports itself as skipped and the chain part runs in the CLI's cluster; where
# neither is reachable the database parts fail rather than pass.
#
# Every database it creates is a disposable origenlab_test_<8 hex>, dropped on exit. It never
# opens origenlab_dev (the guards discard that DSN), never writes to origenlab_clean, and makes
# no network call.
#
# Procedure: docs/OPERATIONS.md §4.1 (“Verify it”).

set -uo pipefail

OL_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
export OL_REPO_ROOT
cd "$OL_REPO_ROOT"
# shellcheck source=lib/local_target.sh
. supabase/scripts/lib/local_target.sh
# shellcheck source=lib/cleanroom_verify.sh
. supabase/scripts/lib/cleanroom_verify.sh

PASS=0
FAIL=0
SKIP=0
ok()   { echo "ok    $*"; PASS=$(( PASS + 1 )); }
bad()  { echo "FAIL  $*"; FAIL=$(( FAIL + 1 )); }
skip() { echo "skip  $*"; SKIP=$(( SKIP + 1 )); }
note() { echo "$*" >&2; }
die()  { echo "FAIL: $*" >&2; exit 1; }

# expect_refusal <label> <needle> <command...>
expect_refusal() {
  local label="$1" needle="$2"; shift 2
  local out rc=0
  out="$("$@" 2>&1)" || rc=$?
  if (( rc == 0 )); then bad "$label: exited 0, expected a refusal"; return; fi
  if [[ "$out" != *"$needle"* ]]; then
    bad "$label: refused, but the diagnostic did not mention '$needle'"
    printf '      got: %s\n' "$(printf '%s' "$out" | grep -v 'why it matters' | tail -n 4)"
    return
  fi
  ok "$label"
}

# expect_success <label> <needle> <command...>
expect_success() {
  local label="$1" needle="$2"; shift 2
  local out rc=0
  out="$("$@" 2>&1)" || rc=$?
  if (( rc != 0 )); then
    bad "$label: exited $rc, expected success"
    printf '      got: %s\n' "$(printf '%s' "$out" | grep -v 'why it matters' | tail -n 6)"
    return
  fi
  if [[ "$out" != *"$needle"* ]]; then bad "$label: succeeded, but the output did not mention '$needle'"; return; fi
  ok "$label"
}

# ── --static ───────────────────────────────────────────────────────────────────────────────────

emit_fresh() { python3 supabase/cleanroom/emit_expected_for_tests.py "$@"; }
emit_db()    { python3 supabase/cleanroom/emit_expected_for_tests.py --expected supabase/cleanroom/expected_data_bearing.json "$@"; }
check_db()   { emit_db "$@" | python3 supabase/cleanroom/compare.py supabase/cleanroom/expected_data_bearing.json; }

static_fixture_chain_matches_disk() {
  local want_count want_head got_count got_head
  want_count="$(ol_chain_versions_on_disk | tr ',' '\n' | grep -c .)"
  want_head="$(ol_chain_versions_on_disk | tr ',' '\n' | tail -n 1)"
  got_count="$(emit_fresh | sed -n 's/^migrations\.count|//p')"
  got_head="$(emit_fresh | sed -n 's/^migrations\.head|//p')"
  if [[ "$got_count" == "$want_count" && "$got_head" == "$want_head" ]]; then
    ok "S1: expected_counts.json declares the chain on disk ($want_count migrations, head $want_head)"
  else
    bad "S1: expected_counts.json declares $got_count migrations / head $got_head, but supabase/migrations/ holds $want_count / $want_head — re-declare the fresh baseline in the same PR as the migration"
  fi
}

static_contracts_are_well_formed() {
  local out rc=0
  out="$(python3 - <<'PY' 2>&1
import json, re, sys
from pathlib import Path
root = Path("supabase/cleanroom")
fresh = json.loads((root / "expected_counts.json").read_text())["probes"]
data = json.loads((root / "expected_data_bearing.json").read_text())["probes"]
problems = []

def emitted(sql_path):
    return set(re.findall(r"select '([a-z0-9_.]+)'", (root / sql_path).read_text()))

for name, probes, sql in (("expected_counts.json", fresh, "verify.sql"),
                          ("expected_data_bearing.json", data, "data_bearing.sql")):
    keys = emitted(sql)
    missing = sorted(set(probes) - keys)
    extra = sorted(keys - set(probes))
    if missing:
        problems.append(f"{name} declares probes {sql} never emits: {missing}")
    if extra:
        problems.append(f"{sql} emits probes {name} never declares: {extra}")
    for key, spec in probes.items():
        if not spec.get("why"):
            problems.append(f"{name}: {key} has no 'why'")

# The data-bearing contract counts no business row: every probe it expects is a boolean, a
# structural inventory number, a name list, or a zero-violation count. An integer expectation
# other than 0 is allowed only for the inventory constants the chain decides.
inventory = {"tables.count", "policies.count"}
for key, spec in data.items():
    want = spec["expect"]
    if isinstance(want, int) and want != 0 and key not in inventory:
        problems.append(f"expected_data_bearing.json: {key} expects {want}; a non-zero count outside the inventory constants is a business count and belongs in expected_counts.json")
    if key.startswith(("crm.", "evidence.", "comms.", "catalog.", "procurement.", "source_record.", "assertion.")):
        problems.append(f"expected_data_bearing.json: {key} counts a business table")

if problems:
    print("\n".join(problems)); sys.exit(1)
print(f"fresh {len(fresh)} probes, data-bearing {len(data)} probes, both emitted exactly and reasoned")
PY
)" || rc=$?
  if (( rc == 0 )); then ok "S2: both contracts are emitted exactly by their SQL and carry a reason ($out)"; else bad "S2: $out"; fi
}

run_static() {
  echo "== S: no database =="
  static_fixture_chain_matches_disk
  static_contracts_are_well_formed

  expect_success "S3a: the data-bearing contract passes against itself" \
    "(data-bearing) PASSED — 41 probes, all exact" \
    check_db
  expect_refusal "S3b: a missing migration is named" \
    "migrations.missing_from_ledger: expected <none>, measured 20260929100000" \
    check_db migrations.missing_from_ledger=20260929100000
  expect_refusal "S3c: a readable pin_hash fails" \
    "auth.api_select_pin_hash: expected false, measured true" \
    check_db auth.api_select_pin_hash=true
  expect_refusal "S3d: a fourth SECURITY DEFINER fails" \
    "functions.security_definer: expected" \
    check_db "functions.security_definer=outbound.add_contact_control(text,text,text,text,uuid,uuid,jsonb),platform.begin_pin_attempt(uuid,uuid),platform.finish_pin_attempt(uuid,uuid,uuid,bytea),platform.record_pin_attempt(text,uuid,uuid)"
  expect_refusal "S3e: a probe declared but never measured fails" \
    "auth.api_execute_begin_pin_attempt: declared but never measured" \
    check_db --drop auth.api_execute_begin_pin_attempt
  expect_refusal "S3f: a probe measured but not declared fails" \
    "crm.opportunity: measured but not declared" \
    check_db --add crm.opportunity=44

  guard() { bash -c '. supabase/scripts/lib/local_target.sh; '"$1"; }
  expect_refusal "S4a: the scratch guard refuses origenlab_clean by name" \
    "is not a disposable database name" \
    guard 'ol_require_dev_scratch_database origenlab_clean'
  expect_refusal "S4b: the scratch guard refuses origenlab_dev by name" \
    "is not a disposable database name" \
    guard 'ol_require_dev_scratch_database origenlab_dev'
  expect_refusal "S4c: the scratch guard refuses an empty name" \
    "is not a disposable database name" \
    guard 'ol_require_dev_scratch_database ""'
  expect_refusal "S4d: ol_psql_dev_scratch refuses before the guard" \
    "called before ol_require_dev_scratch_database succeeded" \
    guard 'ol_psql_dev_scratch -c "select 1"'
  expect_refusal "S4e: cleanroom_db.sh verify refuses an unknown mode" \
    "unknown option" \
    supabase/scripts/cleanroom_db.sh verify --today
}

# ── clusters ───────────────────────────────────────────────────────────────────────────────────
#
# One set of scratch primitives, two clusters behind them. `dev` is the development container
# (needed for --restored, because the copy must be made with that container's pg_dump); `cli`
# is the Supabase CLI's cluster, the only one CI has. Every scratch name is origenlab_test_<8
# hex>, and every connection goes through the guard that pins that pattern.

CLUSTER=""
declare -a CREATED=()

cluster_select() {
  local want="$1"
  case "$want" in
    dev)
      ol_require_dev_database "$OL_REPO_ROOT" >/dev/null 2>&1 || die "--cluster dev: the development container is not this working tree's (run from the tree that started it)"
      CLUSTER=dev ;;
    cli)
      ol_require_local_target "$OL_REPO_ROOT" >/dev/null 2>&1 || die "--cluster cli: the local Supabase stack is not running from this working tree"
      CLUSTER=cli ;;
    auto)
      if ol_require_dev_database "$OL_REPO_ROOT" >/dev/null 2>&1; then CLUSTER=dev
      elif ol_require_local_target "$OL_REPO_ROOT" >/dev/null 2>&1; then CLUSTER=cli
      else die "no cluster: neither the development container nor the local Supabase stack belongs to this working tree"; fi ;;
    *) die "unknown cluster '$want'" ;;
  esac
  # Whatever the guard exported for origenlab_dev is discarded: this script never opens it.
  unset OL_DEV_DB_URL
  note "cluster: $CLUSTER"
}

scratch_name() { printf 'origenlab_test_%s\n' "$(od -An -tx1 -N4 /dev/urandom | tr -d ' \n')"; }

# maintenance psql — the cluster's `postgres` database, CREATE/DROP DATABASE only.
mpsql() {
  case "$CLUSTER" in
    dev) ol_require_dev_database "$OL_REPO_ROOT" >/dev/null || return 1; unset OL_DEV_DB_URL
         psql "postgresql://${OL_DEV_SUPERUSER}:postgres@127.0.0.1:${OL_DEV_PORT}/postgres" -X -v ON_ERROR_STOP=1 "$@" ;;
    cli) ol_require_local_target "$OL_REPO_ROOT" >/dev/null || return 1
         ol_psql_maintenance "$@" ;;
  esac
}

# The scratch a verify function is currently bound to. ol_verify_* take a psql function name,
# so the name travels in a variable and `spsql` reads it.
SCRATCH=""
spsql() {
  [[ "$SCRATCH" =~ ^origenlab_test_[0-9a-f]{8}$ ]] || { echo "FAIL: spsql: '$SCRATCH' is not a scratch name" >&2; return 1; }
  case "$CLUSTER" in
    dev) ol_require_dev_scratch_database "$SCRATCH" "$OL_REPO_ROOT" >/dev/null || return 1
         ol_psql_dev_scratch "$@" ;;
    cli) ol_require_local_database "$SCRATCH" "$OL_REPO_ROOT" >/dev/null || return 1
         ol_psql_db "$@" ;;
  esac
}

scratch_create_empty() {
  local name; name="$(scratch_name)"
  mpsql -c "create database $name" >/dev/null || die "could not create $name"
  CREATED+=("$name")
  printf '%s\n' "$name"
}

scratch_clone() {
  local template="$1" name; name="$(scratch_name)"
  [[ "$template" =~ ^origenlab_test_[0-9a-f]{8}$ ]] || die "scratch_clone: '$template' is not a scratch name"
  mpsql -c "create database $name template $template" >/dev/null || die "could not clone $template into $name"
  CREATED+=("$name")
  printf '%s\n' "$name"
}

scratch_drop() {
  local name="$1"
  [[ "$name" =~ ^origenlab_test_[0-9a-f]{8}$ ]] || { note "refusing to drop '$name': not a scratch name"; return 1; }
  mpsql -c "select pg_terminate_backend(pid) from pg_stat_activity where datname = '$name' and pid <> pg_backend_pid()" >/dev/null 2>&1 || true
  mpsql -c "drop database if exists $name" >/dev/null 2>&1 || note "warning: could not drop $name"
}

cleanup() {
  local n
  for n in "${CREATED[@]-}"; do [[ -n "$n" ]] && scratch_drop "$n"; done
}
trap cleanup EXIT

# A scratch carrying the full chain and nothing else.
scratch_seed_chain() {
  local name
  case "$CLUSTER" in
    cli)
      name="$(supabase/scripts/disposable_db.sh mint 2>/dev/null)" || die "could not mint a disposable database"
      CREATED+=("$name") ;;
    dev)
      name="$(scratch_create_empty)"
      SCRATCH="$name"
      ol_bootstrap_platform_objects_into spsql --with-ledger || die "could not bootstrap $name"
      ol_apply_migrations_into spsql 2>/dev/null || die "could not apply the chain into $name" ;;
  esac
  printf '%s\n' "$name"
}

verify_db_on()    { SCRATCH="$1"; ol_verify_data_bearing spsql; }
verify_fresh_on() { SCRATCH="$1"; ol_verify_fresh_rebuild spsql; }

# inject <scratch> <sql>  — one fault, as the role that can commit it.
inject() {
  SCRATCH="$1"
  spsql -q -c "$2" >/dev/null || die "fault injection failed on $1: $2"
}

# ── --chain ────────────────────────────────────────────────────────────────────────────────────

run_chain() {
  echo ""
  echo "== C: a disposable database with the full chain and no business data ($CLUSTER) =="
  local seed
  seed="$(scratch_seed_chain)"
  note "seed scratch: $seed"

  expect_success "C1: the data-bearing contract passes with no business data" \
    "(data-bearing) PASSED — 41 probes, all exact" \
    verify_db_on "$seed"
  expect_refusal "C2: the fresh-rebuild contract still fails without the historical load" \
    "crm.organization: expected 1812, measured 0" \
    verify_fresh_on "$seed"

  local c
  c="$(scratch_clone "$seed")"
  inject "$c" "delete from supabase_migrations.schema_migrations where version = '20260929100000'"
  expect_refusal "C3: a migration missing from the ledger fails closed and is named" \
    "migrations.missing_from_ledger: expected <none>, measured 20260929100000" \
    verify_db_on "$c"
  scratch_drop "$c"

  c="$(scratch_clone "$seed")"
  inject "$c" "insert into supabase_migrations.schema_migrations (version, name) values ('20991231000000', 'phantom')"
  expect_refusal "C4: a ledger row with no file on disk fails closed and is named" \
    "migrations.not_in_supabase_migrations: expected <none>, measured 20991231000000" \
    verify_db_on "$c"
  scratch_drop "$c"

  c="$(scratch_clone "$seed")"
  inject "$c" "set role origenlab_owner; revoke execute on function platform.begin_pin_attempt(uuid, uuid) from origenlab_api"
  expect_refusal "C5: EXECUTE on begin_pin_attempt revoked from the runtime role fails closed" \
    "auth.api_execute_begin_pin_attempt: expected true, measured false" \
    verify_db_on "$c"
  scratch_drop "$c"

  c="$(scratch_clone "$seed")"
  inject "$c" "set role origenlab_owner; grant select (pin_hash) on platform.operator_profile to origenlab_api"
  expect_refusal "C6: a readable pin_hash fails closed" \
    "auth.api_select_pin_hash: expected false, measured true" \
    verify_db_on "$c"
  scratch_drop "$c"

  c="$(scratch_clone "$seed")"
  inject "$c" "set role origenlab_owner; grant update on platform.operator to origenlab_api"
  expect_refusal "C7: a runtime UPDATE on platform.operator fails closed" \
    "auth.api_writes_operator: expected false, measured true" \
    verify_db_on "$c"
  scratch_drop "$c"

  c="$(scratch_clone "$seed")"
  inject "$c" "set role origenlab_owner; alter table platform.auth_session disable row level security"
  expect_refusal "C8: RLS disabled on a sign-in table fails closed" \
    "tables.without_rls: expected 0, measured 1" \
    verify_db_on "$c"
  scratch_drop "$c"

  c="$(scratch_clone "$seed")"
  inject "$c" "set role origenlab_owner; alter function platform.finish_pin_attempt(uuid, uuid, uuid, bytea) reset search_path"
  expect_refusal "C9: a definer without a pinned search_path fails closed" \
    "functions.security_definer_misconfigured: expected 0, measured 1" \
    verify_db_on "$c"
  scratch_drop "$c"

  scratch_drop "$seed"
}

# ── --restored ─────────────────────────────────────────────────────────────────────────────────

cleanroom_fingerprint() {
  # Write counters and the ledger of origenlab_clean, read-only. Reads do not move tup_inserted/updated/deleted.
  ol_psql_clean -X -q -tA -c "begin read only;
    select (select count(*) || '|' || max(version) from supabase_migrations.schema_migrations)
        || '|' || (select tup_inserted || '|' || tup_updated || '|' || tup_deleted from pg_stat_database where datname = current_database());
    commit;" | sed '/^$/d'
}

run_restored() {
  echo ""
  echo "== R: a restored copy of $OL_CLEAN_DBNAME =="
  if [[ "$CLUSTER" != "dev" ]]; then
    skip "R1–R3: the development container is not this working tree's; the restored-copy proof runs only there"
    return
  fi
  ol_require_cleanroom_database "$OL_REPO_ROOT" >/dev/null || die "clean-room guard refused"

  local before after copy rc=0
  before="$(cleanroom_fingerprint)" || die "could not fingerprint $OL_CLEAN_DBNAME"
  copy="$(scratch_create_empty)"
  note "restoring $OL_CLEAN_DBNAME into $copy with the container's pg_dump/pg_restore (no file leaves the container)…"
  docker exec "$OL_DEV_CONTAINER" pg_dump -U supabase_admin --no-password -Fc "$OL_CLEAN_DBNAME" \
    | docker exec -i "$OL_DEV_CONTAINER" pg_restore -U supabase_admin --no-password --exit-on-error -d "$copy" \
    || rc=$?
  if (( rc != 0 )); then
    bad "R1: the restore into $copy failed (rc $rc)"
    return
  fi

  expect_success "R1: the data-bearing contract passes on the restored copy" \
    "(data-bearing) PASSED — 41 probes, all exact" \
    verify_db_on "$copy"
  expect_refusal "R2: the fresh-rebuild contract fails on the restored copy, naming a decision-driven count" \
    "crm.opportunity: expected 0, measured" \
    verify_fresh_on "$copy"

  after="$(cleanroom_fingerprint)" || die "could not re-fingerprint $OL_CLEAN_DBNAME"
  if [[ "$before" == "$after" ]]; then
    ok "R3: $OL_CLEAN_DBNAME is unchanged (ledger|head|ins|upd|del = $after)"
  else
    bad "R3: $OL_CLEAN_DBNAME changed: before $before, after $after"
  fi
  scratch_drop "$copy"
}

# ── dispatch ───────────────────────────────────────────────────────────────────────────────────

DO_STATIC=0 DO_CHAIN=0 DO_RESTORED=0 WANT_CLUSTER=auto
if (( $# == 0 )); then DO_STATIC=1 DO_CHAIN=1 DO_RESTORED=1; fi
while (( $# )); do
  case "$1" in
    --static)   DO_STATIC=1; shift ;;
    --chain)    DO_CHAIN=1; shift ;;
    --restored) DO_RESTORED=1; shift ;;
    --all)      DO_STATIC=1 DO_CHAIN=1 DO_RESTORED=1; shift ;;
    --cluster)  WANT_CLUSTER="${2-}"; shift 2 ;;
    -h|--help)  sed -n '2,50p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown option '$1'" ;;
  esac
done

(( DO_STATIC )) && run_static
if (( DO_CHAIN || DO_RESTORED )); then
  cluster_select "$WANT_CLUSTER"
  (( DO_CHAIN )) && run_chain
  (( DO_RESTORED )) && run_restored
fi

echo ""
echo "─────────────────────────────────────────────"
echo "$PASS passed, $FAIL failed, $SKIP skipped"
(( FAIL == 0 ))
