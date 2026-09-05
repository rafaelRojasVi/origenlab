#!/usr/bin/env bash
# OrigenLab V2 — Slice 0 behavioural role proofs with REAL LOGIN connections.
#
# docs/MIGRATION.md §5.2 checks 6-9; docs/ARCHITECTURE.md §6, §6.2. Complements supabase/tests:
# pgTAP runs as one login, and PostgreSQL authorises SET ROLE against the session user, so "the API
# login cannot assume the owner" and "session_user inside a SECURITY DEFINER call is the runtime
# login" can only be proven by connecting as those logins.
#
# Requirements: a running local stack (`supabase start`), `psql`. The three LOGIN roles are created
# without a password by supabase/roles.sql; this script sets throw-away random passwords for the
# duration of the run and clears them on exit (trap). Nothing here prints a password, a key or a
# connection string. The definer probe is created, exercised and dropped inside this run; the
# rolled-back version of the same proof is supabase/tests/070_definer_semantics.sql.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

command -v psql >/dev/null 2>&1 || { echo "FAIL: psql is required" >&2; exit 2; }

# Admin connection (the CLI's local postgres login). Never echoed.
DB_URL="$(supabase status -o env 2>/dev/null | sed -n 's/^DB_URL=//p' | tr -d '"')"
if [[ -z "$DB_URL" ]]; then
  echo "FAIL: the local stack is not running (supabase start)" >&2
  exit 2
fi
HOSTPORT="$(sed -E 's#^[a-z]+://[^@]+@([^/]+)/.*$#\1#' <<<"$DB_URL")"
DBNAME="$(sed -E 's#^[a-z]+://[^/]+/([^/?]+).*$#\1#' <<<"$DB_URL")"

random_secret() { head -c 48 /dev/urandom | base64 | tr -d '/+=\n' | head -c 40; }
PW_API="$(random_secret)"
PW_WORKER="$(random_secret)"
PW_MIGRATOR="$(random_secret)"

admin_sql() { psql "$DB_URL" -X -q -v ON_ERROR_STOP=1 -f - ; }

cleanup() {
  set +e
  admin_sql <<'SQL' >/dev/null 2>&1
alter role origenlab_api password null;
alter role origenlab_worker password null;
alter role origenlab_migrator password null;
set role origenlab_owner;
drop function if exists outbound.__probe_login_identity();
reset role;
SQL
}
trap cleanup EXIT

admin_sql <<SQL >/dev/null
alter role origenlab_api password '$PW_API';
alter role origenlab_worker password '$PW_WORKER';
alter role origenlab_migrator password '$PW_MIGRATOR';
SQL

PASS=0; FAIL=0
report() { # status name detail
  if [[ "$1" == PASS ]]; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); fi
  printf '%-4s %s%s\n' "$1" "$2" "${3:+  [$3]}"
}

# run_as ROLE PASSWORD SQL -> prints "ok:<last output line>" or "<SQLSTATE>"
run_as() {
  local role="$1" pw="$2" sql="$3" out rc
  out="$(PGPASSWORD="$pw" psql "postgresql://${role}@${HOSTPORT}/${DBNAME}" -X -q -A -t -v ON_ERROR_STOP=1 \
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

echo "== direct logins: origenlab_api =="
expect origenlab_api "$PW_API" "api: connects as itself (session_user|current_user)" "ok:origenlab_api|origenlab_api" "select session_user || '|' || current_user"
expect origenlab_api "$PW_API" "api: cannot SET ROLE origenlab_owner" 42501 "set role origenlab_owner"
expect origenlab_api "$PW_API" "api: cannot SET ROLE origenlab_worker" 42501 "set role origenlab_worker"
expect origenlab_api "$PW_API" "api: cannot SET ROLE origenlab_migrator" 42501 "set role origenlab_migrator"
expect origenlab_api "$PW_API" "api: reads crm.organization" ok "select count(*) from crm.organization"
expect origenlab_api "$PW_API" "api: reads outbound.send_control" ok "select marketing_enabled, transactional_enabled from outbound.send_control"
expect origenlab_api "$PW_API" "api: may insert crm.opportunity (privilege check, zero rows)" ok "insert into crm.opportunity (title, stage, owner_operator_id) select 'x', 'lead', gen_random_uuid() where false"
expect origenlab_api "$PW_API" "api: cannot flip a send flag directly" 42501 "update outbound.send_control set marketing_enabled = true where id = 1"
expect origenlab_api "$PW_API" "api: cannot write outbound.send_attempt" 42501 "insert into outbound.send_attempt (purpose, mailbox_id, address_norm) select 'marketing', gen_random_uuid(), 'a@example.test' where false"
expect origenlab_api "$PW_API" "api: cannot write outbound.contact_control" 42501 "insert into outbound.contact_control (scope, value_norm, kind, purpose, reason, source) select 'address', 'a@example.test', 'block', 'all', 'r', 'operator_command' where false"
expect origenlab_api "$PW_API" "api: cannot write comms.mailbox" 42501 "insert into comms.mailbox (address_norm) select 'm@example.test' where false"
expect origenlab_api "$PW_API" "api: cannot DELETE crm.domain_event" 42501 "delete from crm.domain_event where false"

echo "== direct logins: origenlab_worker =="
expect origenlab_worker "$PW_WORKER" "worker: connects as itself" "ok:origenlab_worker|origenlab_worker" "select session_user || '|' || current_user"
expect origenlab_worker "$PW_WORKER" "worker: cannot SET ROLE origenlab_owner" 42501 "set role origenlab_owner"
expect origenlab_worker "$PW_WORKER" "worker: cannot SET ROLE origenlab_api" 42501 "set role origenlab_api"
expect origenlab_worker "$PW_WORKER" "worker: reads crm.quote_revision" ok "select count(*) from crm.quote_revision"
expect origenlab_worker "$PW_WORKER" "worker: may insert comms.mailbox" ok "insert into comms.mailbox (address_norm) select 'm@example.test' where false"
expect origenlab_worker "$PW_WORKER" "worker: may insert evidence.source_record" ok "insert into evidence.source_record (kind, dedupe_key, payload) select 'workbook_import', 'k', '{}'::jsonb where false"
expect origenlab_worker "$PW_WORKER" "worker: cannot write crm.organization" 42501 "insert into crm.organization (kind, name, confirmation) select 'company', 'w', 'confirmed' where false"
expect origenlab_worker "$PW_WORKER" "worker: cannot write crm.domain_event" 42501 "insert into crm.domain_event (aggregate_kind, aggregate_id, seq, event_type, payload_version, payload, actor_kind) select 'task', gen_random_uuid(), 1, 'task.created', 1, '{}'::jsonb, 'worker' where false"
expect origenlab_worker "$PW_WORKER" "worker: cannot write outbound.send_attempt" 42501 "insert into outbound.send_attempt (purpose, mailbox_id, address_norm) select 'marketing', gen_random_uuid(), 'a@example.test' where false"
expect origenlab_worker "$PW_WORKER" "worker: cannot write quote_revision.pdf_sha256 directly" 42501 "update crm.quote_revision set pdf_sha256 = null where false"

echo "== direct logins: origenlab_migrator =="
expect origenlab_migrator "$PW_MIGRATOR" "migrator: connects as itself" "ok:origenlab_migrator|origenlab_migrator" "select session_user || '|' || current_user"
expect origenlab_migrator "$PW_MIGRATOR" "migrator: holds no privilege of its own (NOINHERIT, no grants)" 42501 "select 1 from crm.organization"
expect origenlab_migrator "$PW_MIGRATOR" "migrator: may SET ROLE origenlab_owner (current_user|session_user)" "ok:origenlab_owner|origenlab_migrator" "set role origenlab_owner; select current_user || '|' || session_user"
expect origenlab_migrator "$PW_MIGRATOR" "migrator: as owner, reaches the application tables" ok "set role origenlab_owner; select count(*) from crm.organization"
expect origenlab_migrator "$PW_MIGRATOR" "migrator: cannot SET ROLE origenlab_api" 42501 "set role origenlab_api"
expect origenlab_migrator "$PW_MIGRATOR" "migrator: cannot SET ROLE origenlab_worker" 42501 "set role origenlab_worker"

echo "== platform roles cannot log in =="
for r in anon authenticated service_role origenlab_owner; do
  if PGPASSWORD="$PW_API" psql "postgresql://${r}@${HOSTPORT}/${DBNAME}" -X -q -A -t -c 'select 1' >/dev/null 2>&1; then
    report FAIL "$r: connection refused" "a connection succeeded"
  else
    report PASS "$r: connection refused (NOLOGIN or no credential)"
  fi
done

echo "== check 9 with real logins: SECURITY DEFINER semantics =="
admin_sql <<'SQL' >/dev/null
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
admin_sql <<'SQL' >/dev/null
set role origenlab_owner;
drop function outbound.__probe_login_identity();
reset role;
SQL
remaining="$(psql "$DB_URL" -X -q -A -t -c "select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname in ('crm','comms','outbound','evidence','catalog','procurement','platform') and p.prosecdef")"
[[ "$remaining" == "0" ]] && report PASS "no SECURITY DEFINER function remains after the probe" || report FAIL "no SECURITY DEFINER function remains after the probe" "count $remaining"

echo
echo "direct-login proofs: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
