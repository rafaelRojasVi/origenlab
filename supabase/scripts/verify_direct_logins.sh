#!/usr/bin/env bash
# OrigenLab V2 — Slice 0 behavioural role proofs with REAL LOGIN connections.
#
# docs/MIGRATION.md §5.2 checks 6-9; docs/ARCHITECTURE.md §6, §6.2, §6.4, §6.5. Complements
# supabase/tests: pgTAP runs as one login, and PostgreSQL authorises SET ROLE against the session
# user, so "the API login cannot assume the owner" and "session_user inside a SECURITY DEFINER call
# is the runtime login" can only be proven by connecting as those logins.
#
# Target: resolved and validated exclusively by supabase/scripts/lib/local_target.sh — a loopback
# host on this project's local port, discovered from `supabase status`, with the inherited libpq
# environment scrubbed so it cannot redirect anything. No connection is attempted if validation
# fails.
#
# Credentials: the three LOGIN roles are created without a password by supabase/roles.sql. This
# script clears any stale password first, sets throw-away random ones for the duration of the run,
# and clears them again through an EXIT trap whose failure is fatal: cleanup errors are reported,
# the absence of every password is re-asserted from pg_authid, and either failure makes the final
# exit status non-zero. Nothing here prints a password, a key or a connection string.
#
# The definer probe is created, exercised and dropped inside this run; the rolled-back version of
# the same proof is supabase/tests/070_definer_semantics.sql.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
# shellcheck source=lib/local_target.sh
source "$ROOT/supabase/scripts/lib/local_target.sh"

command -v psql >/dev/null 2>&1 || { echo "FAIL: psql is required" >&2; exit 2; }

ol_require_local_target "$ROOT" || { echo "FAIL: local target validation failed; nothing was executed" >&2; exit 2; }

WORK="$(mktemp -d)"

ROLES=(origenlab_api origenlab_worker origenlab_migrator origenlab_owner)

random_secret() { head -c 48 /dev/urandom | base64 | tr -d '/+=\n' | head -c 40; }
PW_API="$(random_secret)"
PW_WORKER="$(random_secret)"
PW_MIGRATOR="$(random_secret)"

PASS=0; FAIL=0
report() { # status name detail
  if [[ "$1" == PASS ]]; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi
  printf '%-4s %s%s\n' "$1" "$2" "${3:+  [$3]}"
}

# Clear every temporary password and drop the probe. Errors are surfaced, never discarded.
clear_credentials() { # $1 = context label -> 0 on success
  local log="$WORK/cleanup.log" rc=0 r sql=""
  for r in "${ROLES[@]}"; do sql+="alter role $r password null;"$'\n'; done
  sql+="set role origenlab_owner;"$'\n'
  sql+="drop function if exists outbound.__probe_login_identity();"$'\n'
  sql+="reset role;"$'\n'
  ol_psql -q -f - >"$log" 2>&1 <<<"$sql" || rc=$?
  if (( rc != 0 )); then
    echo "FAIL: clearing temporary credentials ($1) exited $rc" >&2
    ol_sanitize <"$log" | tail -n 20 >&2
    return 1
  fi
  return 0
}

# Catalogue proof that no OrigenLab role carries a password. Reads pg_authid; prints no value.
assert_no_passwords() { # $1 = context label -> 0 on success
  local n rc=0
  n="$(ol_psql -q -A -t -c \
      "select count(*) from pg_authid where rolname like 'origenlab\\_%' and rolpassword is not null" \
      2>"$WORK/authid.log")" || rc=$?
  if (( rc != 0 )); then
    echo "FAIL: could not read pg_authid to verify that no password remains ($1)" >&2
    ol_sanitize <"$WORK/authid.log" | tail -n 10 >&2
    return 1
  fi
  if [[ "$n" != "0" ]]; then
    echo "FAIL: $n of the four OrigenLab roles still carries a password ($1)" >&2
    return 1
  fi
  printf '%-4s %s\n' "ok" "no OrigenLab role carries a password ($1) — pg_authid.rolpassword is null for all ${#ROLES[@]}"
  return 0
}

CLEANED=0
cleanup() {
  local rc=$?              # the status the script would otherwise have exited with
  trap - EXIT
  if (( CLEANED == 0 )); then
    CLEANED=1
    clear_credentials "EXIT trap" || rc=1
    assert_no_passwords "EXIT trap, after cleanup" || rc=1
  fi
  rm -rf "$WORK"
  exit "$rc"
}
trap cleanup EXIT

# Fail closed before the suite: any password left behind by an earlier interrupted run is cleared,
# and its absence proven, before a new one is set.
clear_credentials "pre-run" || exit 3
assert_no_passwords "pre-run, before any password is set" || exit 3

ol_psql -q -f - >/dev/null <<SQL
alter role origenlab_api      password '$PW_API';
alter role origenlab_worker   password '$PW_WORKER';
alter role origenlab_migrator password '$PW_MIGRATOR';
SQL

# run_as ROLE PASSWORD SQL -> prints "ok:<last output line>" or "<SQLSTATE>"
run_as() {
  local role="$1" pw="$2" sql="$3" out rc
  out="$(PGPASSWORD="$pw" psql "postgresql://${role}@${OL_HOSTPORT}/${OL_DB_NAME}" -X -q -A -t -v ON_ERROR_STOP=1 \
        -c '\set VERBOSITY verbose' -c "$sql" 2>&1)" && rc=0 || rc=$?
  if [[ $rc -eq 0 ]]; then
    printf 'ok:%s' "$(tail -n 1 <<<"$out")"
  else
    # verbose errors read "ERROR:  42501: permission denied ..."
    sed -n -E 's/^.*ERROR:  ([0-9A-Z]{5}):.*$/\1/p' <<<"$out" | head -n 1
  fi
}

expect() { # ROLE PASSWORD NAME EXPECTED SQL   (EXPECTED = 'ok', 'ok:<value>' or a SQLSTATE)
  local role="$1" pw="$2" name="$3" want="$4" sql="$5" got
  got="$(run_as "$role" "$pw" "$sql")"
  if [[ "$want" == ok ]]; then
    [[ "$got" == ok:* ]] && report PASS "$name" || report FAIL "$name" "got ${got:-connection failure}"
  else
    [[ "$got" == "$want" ]] && report PASS "$name" || report FAIL "$name" "want $want, got ${got:-connection failure}"
  fi
}

# A statement-acceptance probe is `... where false`: it proves the grant, the RLS policy and the
# statement's privilege check all admit the statement, and nothing else. It writes no row, so it
# is NOT a behavioural WITH CHECK test. The behavioural RLS proofs — a real row seeded, read
# through a policy, and made invisible when the policy is dropped — are
# supabase/tests/050_rls.sql; a representative subset of real-row operations, rolled back, is
# below under "real rows".
PROBE='statement-acceptance probe, writes no row'

echo "== direct logins: origenlab_api =="
expect origenlab_api "$PW_API" "api: connects as itself (session_user|current_user)" "ok:origenlab_api|origenlab_api" "select session_user || '|' || current_user"
expect origenlab_api "$PW_API" "api: cannot SET ROLE origenlab_owner" 42501 "set role origenlab_owner"
expect origenlab_api "$PW_API" "api: cannot SET ROLE origenlab_worker" 42501 "set role origenlab_worker"
expect origenlab_api "$PW_API" "api: cannot SET ROLE origenlab_migrator" 42501 "set role origenlab_migrator"
expect origenlab_api "$PW_API" "api: reads crm.organization" ok "select count(*) from crm.organization"
expect origenlab_api "$PW_API" "api: reads outbound.send_control" ok "select marketing_enabled, transactional_enabled from outbound.send_control"
expect origenlab_api "$PW_API" "api: INSERT on crm.opportunity is admitted ($PROBE)" ok "insert into crm.opportunity (title, stage, owner_operator_id) select 'x', 'lead', gen_random_uuid() where false"
expect origenlab_api "$PW_API" "api: cannot flip a send flag directly (real-row UPDATE)" 42501 "update outbound.send_control set marketing_enabled = true where id = 1"
expect origenlab_api "$PW_API" "api: cannot write outbound.send_attempt (real-row INSERT)" 42501 "insert into outbound.send_attempt (purpose, mailbox_id, address_norm) values ('marketing', gen_random_uuid(), 'a@example.test')"
expect origenlab_api "$PW_API" "api: cannot write outbound.contact_control ($PROBE)" 42501 "insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source) select 'address', 'a@example.test', 'block', 'all', 'r', 'operator_command' where false"
expect origenlab_api "$PW_API" "api: cannot write comms.mailbox (real-row INSERT)" 42501 "insert into comms.mailbox (address_norm) values ('m@example.test')"
expect origenlab_api "$PW_API" "api: cannot DELETE crm.domain_event" 42501 "delete from crm.domain_event where false"

echo "== direct logins: origenlab_worker =="
expect origenlab_worker "$PW_WORKER" "worker: connects as itself" "ok:origenlab_worker|origenlab_worker" "select session_user || '|' || current_user"
expect origenlab_worker "$PW_WORKER" "worker: cannot SET ROLE origenlab_owner" 42501 "set role origenlab_owner"
expect origenlab_worker "$PW_WORKER" "worker: cannot SET ROLE origenlab_api" 42501 "set role origenlab_api"
expect origenlab_worker "$PW_WORKER" "worker: reads crm.quote_revision" ok "select count(*) from crm.quote_revision"
expect origenlab_worker "$PW_WORKER" "worker: INSERT on evidence.source_record is admitted ($PROBE)" ok "insert into evidence.source_record (kind, dedupe_key, payload) select 'workbook_import', 'k', '{}'::jsonb where false"
expect origenlab_worker "$PW_WORKER" "worker: cannot write crm.organization (real-row INSERT)" 42501 "insert into crm.organization (kind, name, confirmation) values ('company', 'worker probe', 'machine_proposed')"
expect origenlab_worker "$PW_WORKER" "worker: cannot write crm.domain_event ($PROBE)" 42501 "insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind) select 'task', gen_random_uuid(), 1, 'task.created', 1, '{}'::jsonb, 'worker' where false"
expect origenlab_worker "$PW_WORKER" "worker: cannot write outbound.send_attempt (real-row INSERT)" 42501 "insert into outbound.send_attempt (purpose, mailbox_id, address_norm) values ('marketing', gen_random_uuid(), 'a@example.test')"
expect origenlab_worker "$PW_WORKER" "worker: cannot write quote_revision.pdf_sha256 directly" 42501 "update crm.quote_revision set pdf_sha256 = null where false"

echo "== real rows: grant + policy admit a genuine write, and the row is rolled back =="
expect origenlab_api "$PW_API" "api: really inserts a crm.organization row inside a transaction, then rolls it back" \
  "ok:inserted:1" \
  "begin; insert into crm.organization (kind, name, confirmation) values ('company', 'direct-login probe', 'machine_proposed'); select 'inserted:' || count(*)::text from crm.organization where name = 'direct-login probe'; rollback;"
expect origenlab_api "$PW_API" "api: the rolled-back organization row is absent in a new session" \
  "ok:0" "select count(*) from crm.organization where name = 'direct-login probe'"
expect origenlab_worker "$PW_WORKER" "worker: really inserts a comms.mailbox row inside a transaction, then rolls it back" \
  "ok:inserted:1" \
  "begin; insert into comms.mailbox (address_norm) values ('probe@example.test'); select 'inserted:' || count(*)::text from comms.mailbox where address_norm = 'probe@example.test'; rollback;"
expect origenlab_worker "$PW_WORKER" "worker: the rolled-back mailbox row is absent in a new session" \
  "ok:0" "select count(*) from comms.mailbox where address_norm = 'probe@example.test'"

echo "== direct logins: origenlab_migrator =="
expect origenlab_migrator "$PW_MIGRATOR" "migrator: connects as itself" "ok:origenlab_migrator|origenlab_migrator" "select session_user || '|' || current_user"
expect origenlab_migrator "$PW_MIGRATOR" "migrator: holds no privilege of its own (NOINHERIT, no grants)" 42501 "select 1 from crm.organization"
expect origenlab_migrator "$PW_MIGRATOR" "migrator: may SET ROLE origenlab_owner (current_user|session_user)" "ok:origenlab_owner|origenlab_migrator" "set role origenlab_owner; select current_user || '|' || session_user"
expect origenlab_migrator "$PW_MIGRATOR" "migrator: as owner, reaches the application tables" ok "set role origenlab_owner; select count(*) from crm.organization"
expect origenlab_migrator "$PW_MIGRATOR" "migrator: cannot SET ROLE origenlab_api" 42501 "set role origenlab_api"
expect origenlab_migrator "$PW_MIGRATOR" "migrator: cannot SET ROLE origenlab_worker" 42501 "set role origenlab_worker"
expect origenlab_migrator "$PW_MIGRATOR" "migrator: as owner, holds no CREATE on the database" \
  "ok:false" "set role origenlab_owner; select has_database_privilege(current_database(), 'CREATE')::text"

# NOLOGIN is a catalogue fact and is asserted as one. A refused connection proves only that the
# supplied credentials were rejected — a wrong password produces the same refusal — so it is
# recorded as corroboration, never as the proof.
echo "== platform and ownership roles: NOLOGIN, asserted from pg_roles =="
for r in anon authenticated service_role origenlab_owner; do
  canlogin="$(ol_psql -q -A -t -c "select rolcanlogin from pg_roles where rolname = '$r'" 2>/dev/null || true)"
  if [[ "$canlogin" == "f" ]]; then
    report PASS "$r: pg_roles.rolcanlogin is false (NOLOGIN)"
  else
    report FAIL "$r: pg_roles.rolcanlogin is false (NOLOGIN)" "got '${canlogin:-role missing}'"
  fi
done
for r in anon authenticated service_role origenlab_owner; do
  if PGPASSWORD="$PW_API" psql "postgresql://${r}@${OL_HOSTPORT}/${OL_DB_NAME}" -X -q -A -t -c 'select 1' >/dev/null 2>&1; then
    report FAIL "$r: a connection with the supplied credentials is refused" "a connection succeeded"
  else
    report PASS "$r: a connection with the supplied credentials is refused (corroboration; NOLOGIN is proven above)"
  fi
done

echo "== check 9 with real logins: SECURITY DEFINER semantics =="
ol_psql -q -f - >/dev/null <<'SQL'
set role origenlab_owner;
create function outbound.__probe_login_identity() returns text
language sql security definer set search_path = pg_catalog
as $f$ select current_user::text || '|' || session_user::text $f$;
revoke all on function outbound.__probe_login_identity() from public, anon, authenticated, service_role;
grant execute on function outbound.__probe_login_identity() to origenlab_worker;
reset role;
SQL
expect origenlab_worker "$PW_WORKER" "worker: inside a definer call current_user is origenlab_owner, session_user is origenlab_worker" "ok:origenlab_owner|origenlab_worker" "select outbound.__probe_login_identity()"
expect origenlab_api "$PW_API" "api: refused EXECUTE on the worker-only definer function" 42501 "select outbound.__probe_login_identity()"
ol_psql -q -f - >/dev/null <<'SQL'
set role origenlab_owner;
drop function outbound.__probe_login_identity();
reset role;
SQL
remaining="$(ol_psql -q -A -t -c "select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname in ('crm','comms','outbound','evidence','catalog','procurement','platform') and p.prosecdef")"
[[ "$remaining" == "0" ]] && report PASS "no SECURITY DEFINER function remains after the probe" || report FAIL "no SECURITY DEFINER function remains after the probe" "count $remaining"

echo "== no permanent fixture was left behind =="
leftover="$(ol_psql -q -A -t -c "select (select count(*) from crm.organization) + (select count(*) from comms.mailbox)")"
[[ "$leftover" == "0" ]] && report PASS "crm.organization and comms.mailbox are empty: every real-row probe rolled back" \
  || report FAIL "crm.organization and comms.mailbox are empty" "rows: $leftover"

echo "== temporary credentials are cleared, and their absence proven again =="
cleanup_rc=0
clear_credentials "end of the suite" || cleanup_rc=1
assert_no_passwords "end of the suite, after the complete test suite" || cleanup_rc=1
if (( cleanup_rc == 0 )); then
  CLEANED=1   # the EXIT trap has nothing left to do; it still runs if anything below fails
  report PASS "every temporary password is cleared and pg_authid proves none of the four OrigenLab roles retains one"
else
  report FAIL "every temporary password is cleared and pg_authid proves none of the four OrigenLab roles retains one" "cleanup failed"
fi

echo
echo "direct-login proofs: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
