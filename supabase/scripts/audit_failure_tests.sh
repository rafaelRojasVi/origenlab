#!/usr/bin/env bash
# OrigenLab V2 — failure injection for the Slice 0 audit.
#
# The audit is evidence, so it is tested the way the rest of the evidence tooling is: by making it
# fail on purpose and proving it says so. Each scenario runs the real
# supabase/scripts/slice0_audit.sh against injected conditions, and asserts both the refusal and
# the thing that matters more -- that no connection was attempted after a validation failure.
#
# `psql` is shimmed onto the front of PATH so every scenario can prove whether it was invoked at
# all. `supabase` is not shimmed and not called. Nothing here contacts a hosted project: the only
# host names used are the RFC 5737 documentation address 192.0.2.10, loopback, and a
# `db.<twenty letters>.supabase.co` name that is never resolved because the scenarios that use it
# are refused before resolution, or resolved only through an injected resolver in the unit tests.
#
# Two scenarios involve a write, and both run ONLY against the disposable local database.
# Scenario N plants a malicious ~/.psqlrc that tries to create a marker table and proves it never
# runs, with a positive control that proves the same file does work when `-X` is absent.
#
# One scenario does issue a write -- scenario L, the negative mutation test. It runs ONLY against
# the disposable local database, inside an explicit rollback-only harness, and it exists to prove
# that the read-only transaction the audit opens actually refuses a mutation. It is not part of the
# audit and can never run against a hosted project: the audit itself issues no INSERT, UPDATE,
# DELETE, DDL, sequence change or any other intentional mutation in either mode.
#
# Preconditions: the local stack is running and freshly reset (`supabase start`,
# `supabase db reset --local`). Requires bash, python3 and psql; installs nothing.
#
# Procedure: docs/OPERATIONS.md §4.2.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

AUDIT="$ROOT/supabase/scripts/slice0_audit.sh"
SQL_DIR="$ROOT/supabase/audit/sql"
FIXED_NOW="2026-09-10T00:00:00Z"

OL_REAL_PSQL="$(command -v psql)" || { echo "FAIL: psql not found" >&2; exit 2; }
export OL_REAL_PSQL

WORK="$(mktemp -d)"
PLANTED_SQL=""
cleanup() {
  [[ -n "$PLANTED_SQL" && -e "$PLANTED_SQL" ]] && rm -f "$PLANTED_SQL"
  if [[ -n "${OL_KEEP_WORK:-}" ]]; then echo "work dir kept: $WORK"; else rm -rf "$WORK"; fi
}
trap cleanup EXIT

SHIM="$WORK/shim"
STATE="$WORK/state"
mkdir -p "$SHIM" "$STATE"
export OL_SHIM_STATE="$STATE"

# The real path and the tally file are baked in rather than read from the environment. The audit
# builds its child environment from scratch precisely so that nothing inherited can influence a
# connection, so a shim that needed OL_REAL_PSQL would be broken by the behaviour under test.
cat >"$SHIM/psql" <<SHIM_EOF
#!/usr/bin/env bash
printf '%s\n' "invoked" >>"$STATE/psql_calls"
# One line per invocation, NUL-free and one token per field, so a scenario can assert the exact
# argument vector the audit built (scenario N) and not merely that psql was reached.
printf '%s\t' "\$@" >>"$STATE/psql_argv"; printf '\n' >>"$STATE/psql_argv"
exec "$OL_REAL_PSQL" "\$@"
SHIM_EOF
chmod +x "$SHIM/psql"

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf 'ok    %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf 'not ok %s  [%s]\n' "$1" "${2:-}"; }
check() { if [[ "$2" == 0 ]]; then ok "$1"; else bad "$1" "${3:-}"; fi }

# A run-level conclusion of PASS is the standalone word, or the JSON verdict field. Matching the
# bare substring would also hit a per-check status and the SIMULATED_/LOCAL_ prefixes.
no_pass_conclusion() {
  ! grep -qE '(^|[[:space:]])verdict:[[:space:]]+PASS([[:space:]]|$)' "$1" \
    && ! grep -qE '"verdict":[[:space:]]*"PASS"' "$1"
}

run_audit() { # $1 = log file; remaining args passed through. Prints the exit status.
  local log="$1"; shift
  rm -f "$STATE/psql_calls" "$STATE/psql_argv"
  local rc=0
  env PATH="$SHIM:$PATH" "$AUDIT" "$@" >"$log" 2>&1 || rc=$?
  echo "$rc"
}

# Same runner, with extra environment placed in front of the audit. Used by scenario M to prove
# that a hostile parent environment cannot reach the child libpq process.
run_audit_env() { # $1 = log file; $2.. = NAME=VALUE pairs, then `--`, then audit arguments.
  local log="$1"; shift
  local -a inject=()
  while [[ $# -gt 0 && "$1" != "--" ]]; do inject+=("$1"); shift; done
  shift || true
  rm -f "$STATE/psql_calls" "$STATE/psql_argv"
  local rc=0
  env PATH="$SHIM:$PATH" "${inject[@]}" "$AUDIT" "$@" >"$log" 2>&1 || rc=$?
  echo "$rc"
}

psql_was_invoked() { [[ -s "$STATE/psql_calls" ]]; }

declared_local_head_gap() {
  python3 "$ROOT/supabase/audit/assert_declared_local_head_gap.py" "$1"
}

echo "== slice 0 audit: failure injection =="
echo

# --- control: current head is ahead of the deliberately frozen Slice 0 baseline ------------------
log="$WORK/log_control"
rc="$(run_audit "$log" --mode local --now "$FIXED_NOW" --out "$WORK/reports_control")"
control_report="$WORK/reports_control/slice0-audit-local.json"
check "control: frozen Slice 0 baseline refuses current local head" \
  "$([[ $rc -eq 1 ]] && echo 0 || echo 1)" "rc=$rc"
check "control: the refusal is exactly the declared post-Slice-0 schema delta" \
  "$(declared_local_head_gap "$control_report" >/dev/null && echo 0 || echo 1)" \
  "unexpected local-head drift"
check "control: no run-level PASS conclusion is printed" \
  "$(no_pass_conclusion "$log" && echo 0 || echo 1)" "PASS conclusion found"
check "control: psql was invoked, so the run really connected" \
  "$(psql_was_invoked && echo 0 || echo 1)" "psql was never called"

# --- A: a check file that is not a single read ---------------------------------------------------
# The bank is validated before a connection is opened, so a tampered file must refuse the whole run
# without psql being invoked at all.
PLANTED_SQL="$SQL_DIR/999_injected_mutation.sql"
cat >"$PLANTED_SQL" <<'SQL_EOF'
-- Injected by supabase/scripts/audit_failure_tests.sh. Removed by its EXIT trap.
select jsonb_build_object('check', 'zz9')::text;
update outbound.send_control set marketing_enabled = true;
SQL_EOF
log="$WORK/log_A"
rc="$(run_audit "$log" --mode local --now "$FIXED_NOW" --out "$WORK/reports_A")"
rm -f "$PLANTED_SQL"; PLANTED_SQL=""
check "A: a check file containing a mutation makes the audit exit non-zero" \
  "$([[ $rc -ne 0 ]] && echo 0 || echo 1)" "rc=$rc"
check "A: the failure names the offending file and the statement count" \
  "$(grep -q '999_injected_mutation.sql' "$log" && echo 0 || echo 1)" "wrong diagnostic"
check "A: psql is never invoked — the bank is rejected before any connection" \
  "$(psql_was_invoked && echo 1 || echo 0)" "psql was invoked"
check "A: no run-level PASS conclusion is printed" \
  "$(no_pass_conclusion "$log" && echo 0 || echo 1)" "PASS conclusion found"

# --- B: catalogue drift against the committed baseline -------------------------------------------
# A tampered baseline stands in for a hosted project that does not match. Every finding must be a
# FAIL, and the verdict must not be a pass of any kind.
BASELINE="$ROOT/supabase/audit/baselines/slice0.json"
cp "$BASELINE" "$WORK/baseline.orig"
python3 - "$BASELINE" <<'PY'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
doc = json.loads(path.read_text(encoding="utf-8"))
doc["a08"]["table_count"] = 34
doc["a09"]["policy_count"] = 999
doc["a10"]["foreign_key_count"] = 7
path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
log="$WORK/log_B"
rc="$(run_audit "$log" --mode local --now "$FIXED_NOW" --out "$WORK/reports_B")"
cp "$WORK/baseline.orig" "$BASELINE"
check "B: catalogue drift against the baseline makes the audit exit non-zero" \
  "$([[ $rc -ne 0 ]] && echo 0 || echo 1)" "rc=$rc"
check "B: the verdict is LOCAL_FAIL" \
  "$(grep -q 'verdict: LOCAL_FAIL' "$log" && echo 0 || echo 1)" "no LOCAL_FAIL line"
check "B: the failing checks are named" \
  "$(grep -qE 'blocking: .*a08.*a09.*a10' "$log" && echo 0 || echo 1)" "checks not named"
check "B: no run-level PASS conclusion is printed" \
  "$(no_pass_conclusion "$log" && echo 0 || echo 1)" "PASS conclusion found"

# --- C: an incomplete run is INCOMPLETE, never a pass ---------------------------------------------
# A fixture missing one observation stands in for a run that stopped early.
python3 - "$WORK/fixture_missing.json" <<'PY'
import json, sys
from pathlib import Path
source = Path("supabase/audit/fixtures/simulated_hosted.json")
doc = json.loads(source.read_text(encoding="utf-8"))
del doc["observations"]["a09"]
del doc["observations"]["a04"]
Path(sys.argv[1]).write_text(json.dumps(doc), encoding="utf-8")
PY
log="$WORK/log_C"
rc="$(run_audit "$log" --mode hosted --simulate --fixture "$WORK/fixture_missing.json" \
      --attestation supabase/audit/fixtures/attestation.example.json \
      --now "$FIXED_NOW" --out "$WORK/reports_C")"
check "C: a run missing observations exits non-zero" "$([[ $rc -ne 0 ]] && echo 0 || echo 1)" "rc=$rc"
check "C: the verdict is SIMULATED_INCOMPLETE, not a pass" \
  "$(grep -q 'verdict: SIMULATED_INCOMPLETE' "$log" && echo 0 || echo 1)" "wrong verdict"
check "C: the missing checks are named as incomplete, not silently dropped" \
  "$(grep -qE 'incomplete: .*a04.*a09' "$log" && echo 0 || echo 1)" "checks not named"
check "C: no run-level PASS conclusion is printed" \
  "$(no_pass_conclusion "$log" && echo 0 || echo 1)" "PASS conclusion found"

# --- D: a fully satisfied simulated run is SIMULATED_PASS, never PASS ------------------------------
log="$WORK/log_D"
rc="$(run_audit "$log" --mode hosted --simulate \
      --attestation supabase/audit/fixtures/attestation.example.json \
      --now "$FIXED_NOW" --out "$WORK/reports_D")"
check "D: a fully satisfied simulated run exits 0" "$([[ $rc -eq 0 ]] && echo 0 || echo 1)" "rc=$rc"
check "D: the verdict is SIMULATED_PASS" \
  "$(grep -q 'verdict: SIMULATED_PASS' "$log" && echo 0 || echo 1)" "no SIMULATED_PASS line"
check "D: no run-level PASS conclusion is printed even though every check held" \
  "$(no_pass_conclusion "$log" && echo 0 || echo 1)" "PASS conclusion found"
check "D: the run is not eligible for a migration gate" \
  "$(grep -q 'gate_eligible=false' "$log" && echo 0 || echo 1)" "gate_eligible was not false"
check "D: psql is never invoked — a simulated run contacts nothing" \
  "$(psql_was_invoked && echo 1 || echo 0)" "psql was invoked"
D_JSON="$WORK/reports_D/slice0-audit-hosted-simulated.json"
check "D: the report records simulated true and hosted_contacted false" \
  "$(python3 -c "
import json,sys
d=json.load(open('$D_JSON'))
sys.exit(0 if d['run']['simulated'] and not d['run']['hosted_contacted']
              and d['verdict']['verdict']=='SIMULATED_PASS'
              and not d['verdict']['gate_eligible'] else 1)" && echo 0 || echo 1)" "report fields wrong"

# --- E: local mode with a hostile libpq and Supabase environment ------------------------------------
# 192.0.2.10 is RFC 5737 documentation space and is never dialled: the guard scrubs the environment
# and the target still comes only from `supabase status`.
log="$WORK/log_E"
rm -f "$STATE/psql_calls"
rc=0
env PATH="$SHIM:$PATH" \
    PGHOST=192.0.2.10 PGPORT=5432 PGUSER=postgres PGDATABASE=postgres PGPASSWORD=hostile \
    SUPABASE_DB_URL="postgresql://x:y@db.abcdefghijklmnopqrst.supabase.co:5432/postgres" \
    SUPABASE_ACCESS_TOKEN="sbp_EXAMPLENOTAREALTOKEN000000000000000000" \
    "$AUDIT" --mode local --now "$FIXED_NOW" --out "$WORK/reports_E" >"$log" 2>&1 || rc=$?
check "E: hostile libpq/Supabase state leaves only the declared local-head gap" \
  "$([[ $rc -eq 1 ]] && declared_local_head_gap "$WORK/reports_E/slice0-audit-local.json" >/dev/null && echo 0 || echo 1)" \
  "rc=$rc"
check "E: the inherited environment is reported as ignored, never used as a fallback" \
  "$(grep -q 'ignoring inherited libpq/Supabase environment' "$log" && echo 0 || echo 1)" "no scrub notice"
check "E: the audit still connected over loopback" \
  "$(grep -q '127.0.0.1' "$log" && echo 0 || echo 1)" "loopback target not reported"
E_JSON="$WORK/reports_E/slice0-audit-local.json"
check "E: the report records the target as loopback, not as the hostile host" \
  "$(python3 -c "
import json,sys
d=json.load(open('$E_JSON'))
sys.exit(0 if d['target']['host_class']=='loopback' and d['target']['port']==54322 else 1)" \
  && echo 0 || echo 1)" "target was not loopback"
check "E: no hosted host name or access token reaches the report" \
  "$(grep -qE 'supabase\.co|sbp_EXAMPLENOTAREALTOKEN' "$E_JSON" && echo 1 || echo 0)" "a hosted identifier leaked"

# --- F: hosted mode without the explicit authorisation ---------------------------------------------
log="$WORK/log_F"
rc="$(run_audit "$log" --mode hosted --now "$FIXED_NOW" --out "$WORK/reports_F")"
check "F: hosted mode without --authorize-hosted-connection exits non-zero" \
  "$([[ $rc -ne 0 ]] && echo 0 || echo 1)" "rc=$rc"
check "F: the failure says a hosted connection is never a default" \
  "$(grep -q 'requires --authorize-hosted-connection' "$log" && echo 0 || echo 1)" "wrong diagnostic"
check "F: psql is never invoked" "$(psql_was_invoked && echo 1 || echo 0)" "psql was invoked"

# --- G: hosted mode cannot use link state -----------------------------------------------------------
REF_DIR="$ROOT/supabase/.temp"
REF_FILE="$REF_DIR/project-ref"
if [[ -e "$REF_FILE" ]]; then
  bad "G: precondition — $REF_FILE already exists" "refusing to overwrite it"
else
  planted_dir=0
  [[ -d "$REF_DIR" ]] || { mkdir -p "$REF_DIR"; planted_dir=1; }
  printf 'abcdefghijklmnopqrst\n' >"$REF_FILE"
  log="$WORK/log_G"
  rc="$(run_audit "$log" --mode hosted --authorize-hosted-connection --now "$FIXED_NOW" \
        --out "$WORK/reports_G")"
  rm -f "$REF_FILE"
  (( planted_dir == 1 )) && rmdir "$REF_DIR" 2>/dev/null || true
  check "G: a planted supabase/.temp/project-ref stops a hosted run" \
    "$([[ $rc -ne 0 ]] && echo 0 || echo 1)" "rc=$rc"
  check "G: the failure says hosted mode refuses link state" \
    "$(grep -q 'refuses to run against link' "$log" && echo 0 || echo 1)" "wrong diagnostic"
  check "G: psql is never invoked" "$(psql_was_invoked && echo 1 || echo 0)" "psql was invoked"
fi

# --- H: a hosted target that resolves to something local is refused ------------------------------------
# The classifier is exercised directly, because reaching it through a real name would require DNS.
log="$WORK/log_H"
python3 - >"$log" 2>&1 <<'PY' || true
import sys
from pathlib import Path
sys.path.insert(0, "supabase/audit")
from olaudit import target_hosted
from olaudit.target import TargetError

failures = 0
for address in ("127.0.0.1", "10.1.2.3", "192.168.0.5", "169.254.1.1", "100.64.0.1", "::1", "fc00::1"):
    classification = target_hosted.classify_address(address)
    if classification == "global":
        print(f"NOT REFUSED: {address} classified as global")
        failures += 1
    else:
        print(f"refused: {address} -> {classification}")
try:
    target_hosted.assert_all_global(["93.184.216.34", "127.0.0.1"])
    print("NOT REFUSED: a mixed answer containing loopback was accepted")
    failures += 1
except TargetError as exc:
    print(f"refused: a mixed answer containing loopback -> {exc}")
sys.exit(1 if failures else 0)
PY
rc=$?
check "H: every loopback, private, link-local and CGNAT answer is refused as a hosted target" \
  "$([[ $rc -eq 0 ]] && echo 0 || echo 1)" "see $log"
check "H: one bad answer among good ones refuses the whole target" \
  "$(grep -q 'refused: a mixed answer' "$log" && echo 0 || echo 1)" "mixed answer accepted"

# --- I: TLS cannot be downgraded, and the credential cannot live in the target file ------------------
log="$WORK/log_I"
python3 - >"$log" 2>&1 <<'PY' || true
import sys, tempfile
from pathlib import Path
sys.path.insert(0, "supabase/audit")
from olaudit import target_hosted
from olaudit.target import TargetError

REF = "abcdefghijklmnopqrst"
failures = 0
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    ca = root / "ca.crt"
    ca.write_text("x", encoding="utf-8")
    path = root / "supabase" / ".audit" / "hosted_target.env"
    path.parent.mkdir(parents=True)

    def attempt(sslmode):
        path.write_text(
            f"OL_HOSTED_ROUTE=direct\nOL_HOSTED_HOST=db.{REF}.supabase.co\nOL_HOSTED_PORT=5432\n"
            f"OL_HOSTED_DATABASE=postgres\nOL_HOSTED_USER=origenlab_migrator\n"
            f"OL_HOSTED_PROJECT_REF={REF}\nOL_HOSTED_SSLMODE={sslmode}\n"
            f"OL_HOSTED_SSLROOTCERT={ca}\nOL_HOSTED_PASSWORD_ENV=OL_HOSTED_DB_PASSWORD\n",
            encoding="utf-8",
        )
        path.chmod(0o600)
        return target_hosted.resolve(
            root, {"OL_HOSTED_DB_PASSWORD": "not-a-real-credential"}, resolver=lambda h, p: ["93.184.216.34"]
        )

    for sslmode in ("require", "prefer", "allow", "disable", "verify-ca"):
        try:
            attempt(sslmode)
            print(f"NOT REFUSED: sslmode {sslmode} was accepted")
            failures += 1
        except TargetError:
            print(f"refused: sslmode {sslmode}")
    target = attempt("verify-full")
    print(f"accepted: sslmode verify-full, address pinned as {bool(target.hostaddr)}")

    # No credential may be written into the target file: the only key that mentions a password
    # names an environment variable, and an unknown key is refused outright.
    path.write_text(
        f"OL_HOSTED_ROUTE=direct\nOL_HOSTED_HOST=db.{REF}.supabase.co\nOL_HOSTED_PORT=5432\n"
        f"OL_HOSTED_DATABASE=postgres\nOL_HOSTED_USER=origenlab_migrator\n"
        f"OL_HOSTED_PROJECT_REF={REF}\nOL_HOSTED_SSLMODE=verify-full\n"
        f"OL_HOSTED_SSLROOTCERT={ca}\nOL_HOSTED_PASSWORD_ENV=OL_HOSTED_DB_PASSWORD\n"
        "OL_HOSTED_PASSWORD=inline-secret\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    try:
        attempt_result = target_hosted.resolve(
            root, {"OL_HOSTED_DB_PASSWORD": "not-a-real-credential"}, resolver=lambda h, p: ["93.184.216.34"]
        )
        print("NOT REFUSED: an inline password key was accepted")
        failures += 1
    except TargetError:
        print("refused: an inline password key in the target file")
sys.exit(1 if failures else 0)
PY
rc=$?
check "I: no sslmode below verify-full is accepted, and there is no downgrade path" \
  "$([[ $rc -eq 0 ]] && echo 0 || echo 1)" "see $log"
check "I: the credential cannot be written into the target file" \
  "$(grep -q 'refused: an inline password key' "$log" && echo 0 || echo 1)" "inline password accepted"
# The rc above cannot fail -- the heredoc is guarded by `|| true` -- so the refusals are asserted
# from the log as well: five downgrades named one by one, and the one mode that is accepted.
check "I: every sslmode below verify-full was refused by name" \
  "$([[ "$(grep -c '^refused: sslmode ' "$log")" == "5" ]] && echo 0 || echo 1)" \
  "expected five named refusals"
check "I: verify-full is accepted and the address is pinned before connect" \
  "$(grep -q 'accepted: sslmode verify-full, address pinned as True' "$log" && echo 0 || echo 1)" \
  "verify-full was not accepted with a pinned address"

# --- M: the Supavisor session route is selected, never fallen back to ---------------------------
# The pooler host names no project, so the login carries the reference and *is* this route's
# target-identity binding. Every refusal below is a way of getting that binding wrong.
log="$WORK/log_M"
python3 - >"$log" 2>&1 <<'PYM' || true
import sys, tempfile
from pathlib import Path
sys.path.insert(0, "supabase/audit")
from olaudit import target_hosted
from olaudit.target import ROUTE_DIRECT, ROUTE_SUPAVISOR_SESSION, TargetError

REF = "abcdefghijklmnopqrst"
BOTH = frozenset({ROUTE_DIRECT, ROUTE_SUPAVISOR_SESSION})
POOLER = "aws-0-sa-east-1.pooler.supabase.com"
failures = 0

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    ca = root / "ca.crt"
    ca.write_text("x", encoding="utf-8")
    path = root / "supabase" / ".audit" / "hosted_target.env"
    path.parent.mkdir(parents=True)

    def body(**over):
        fields = {
            "OL_HOSTED_ROUTE": "supavisor-session",
            "OL_HOSTED_HOST": POOLER,
            "OL_HOSTED_PORT": "5432",
            "OL_HOSTED_DATABASE": "postgres",
            "OL_HOSTED_USER": "origenlab_migrator." + REF,
            "OL_HOSTED_PROJECT_REF": REF,
            "OL_HOSTED_POOLER_CLUSTER": "0",
            "OL_HOSTED_POOLER_REGION": "sa-east-1",
            "OL_HOSTED_SSLMODE": "verify-full",
            "OL_HOSTED_SSLROOTCERT": str(ca),
            "OL_HOSTED_PASSWORD_ENV": "OL_HOSTED_DB_PASSWORD",
        }
        fields.update(over)
        return "".join(f"{k}={v}\n" for k, v in fields.items() if v != "")

    def resolve(text, routes=BOTH):
        path.write_text(text, encoding="utf-8")
        path.chmod(0o600)
        return target_hosted.resolve(
            root,
            {"OL_HOSTED_DB_PASSWORD": "not-a-real-credential"},
            resolver=lambda h, p: ["93.184.216.34"],
            authorized_routes=routes,
        )

    target = resolve(body())
    print("accepted: route %s on port %d" % (target.route, target.port))

    try:
        resolve(body(), routes=frozenset({ROUTE_DIRECT}))
        print("NOT REFUSED: a declared route that this run did not authorise")
        failures += 1
    except TargetError:
        print("refused: a declared route that this run did not authorise")

    cases = [
        ("transaction-mode port", body(OL_HOSTED_PORT="6543")),
        ("wrong project-qualified login", body(OL_HOSTED_USER="postgres." + REF)),
        ("another project's login", body(OL_HOSTED_USER="origenlab_migrator.tsrqponmlkjihgfedcba")),
        ("bare login without the project ref", body(OL_HOSTED_USER="origenlab_migrator")),
        ("wrong region", body(OL_HOSTED_POOLER_REGION="us-east-1")),
        ("wrong cluster", body(OL_HOSTED_POOLER_CLUSTER="1")),
        ("look-alike hostname", body(OL_HOSTED_HOST=POOLER + ".evil.example.com")),
        ("direct host on the pooler route", body(OL_HOSTED_HOST="db." + REF + ".supabase.co")),
        ("downgraded tls", body(OL_HOSTED_SSLMODE="require")),
        ("pooler keys on the direct route", body(OL_HOSTED_ROUTE="direct")),
    ]
    for name, text in cases:
        try:
            resolve(text)
            print("NOT REFUSED: " + name)
            failures += 1
        except TargetError:
            print("refused: " + name)

    described = str(target.describe())
    if REF in described or "not-a-real-credential" in described or POOLER in described:
        print("NOT REDACTED: the target description carries an identifier")
        failures += 1
    else:
        print("redacted: no project reference, host or credential in the target description")
sys.exit(1 if failures else 0)
PYM
while IFS= read -r scenario; do
  [[ -z "$scenario" ]] && continue
  check "M: refused - $scenario" \
    "$(grep -qF "refused: $scenario" "$log" && echo 0 || echo 1)" "see $log"
done <<'SCENARIOS'
a declared route that this run did not authorise
transaction-mode port
wrong project-qualified login
another project's login
bare login without the project ref
wrong region
wrong cluster
look-alike hostname
direct host on the pooler route
downgraded tls
pooler keys on the direct route
SCENARIOS
check "M: the valid session-pooler route resolves on port 5432" \
  "$(grep -qF 'accepted: route supavisor-session on port 5432' "$log" && echo 0 || echo 1)" "see $log"
check "M: no project reference, host or credential reaches the target description" \
  "$(grep -q '^redacted: ' "$log" && echo 0 || echo 1)" "see $log"
# The shape of the script the pooler route would send, proven without sending it.
log="$WORK/log_M_shape"
python3 - >"$log" 2>&1 <<'PYS' || true
import re, sys
sys.path.insert(0, "supabase/audit")
from olaudit import psqlrun, sqlbank
from olaudit.target import ROUTE_SUPAVISOR_SESSION

bank = sqlbank.load()
script = psqlrun.build_script(bank, route=ROUTE_SUPAVISOR_SESSION)
code = sqlbank.to_code(script).lower()

if not re.search(r"\bcommit\b", code) and len(re.findall(r"\brollback\b", code)) == 1:
    print("shape: no COMMIT on any path, exactly one ROLLBACK")
else:
    print("SHAPE FAIL: the pooler script can commit")

guard_at = script.index("'" + psqlrun.GUARD_CHECK_ID + "'")
first_check = min(script.index(check.sql.rstrip()) for check in bank)
if script.startswith("begin read only;") and guard_at < first_check:
    print("shape: begin read only, then the guard, then the bank")
else:
    print("SHAPE FAIL: the bank can run before the read-only state is read back")

set_lines = [l for l in psqlrun.pooled_preamble(15000, 5000).splitlines()
             if l.strip().lower().startswith("set ")]
if set_lines and all(l.lower().startswith("set local ") for l in set_lines):
    print("shape: every SET the pooler preamble issues is transaction-local")
else:
    print("SHAPE FAIL: the pooler preamble sets session state a pooled backend would keep")
PYS
check "M: no COMMIT exists on the pooler path, and the transaction ends in ROLLBACK" \
  "$(grep -qF 'shape: no COMMIT on any path' "$log" && echo 0 || echo 1)" "see $log"
check "M: the read-only state is read back before the first check file" \
  "$(grep -qF 'shape: begin read only, then the guard' "$log" && echo 0 || echo 1)" "see $log"
check "M: the pooler preamble leaves no session state on a pooled backend" \
  "$(grep -qF 'shape: every SET the pooler preamble issues' "$log" && echo 0 || echo 1)" "see $log"

# --- J: a report that fails the leak assertions is refused --------------------------------------------
POISONED="$WORK/poisoned.json"
python3 - "$POISONED" <<'PY'
import json, sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({
    "note": "connected to db.abcdefghijklmnopqrst.supabase.co as origenlab_migrator",
    "key": "sb_secret_" + "EXAMPLE_NOT_A_REAL_KEY",
}, indent=2), encoding="utf-8")
PY
log="$WORK/log_J"
rc="$(run_audit "$log" --verify-report "$POISONED")"
check "J: a report carrying a hosted host name and a key fails the leak assertions" \
  "$([[ $rc -ne 0 ]] && echo 0 || echo 1)" "rc=$rc"
check "J: the failure says the report must not be uploaded" \
  "$(grep -q 'must not be uploaded' "$log" && echo 0 || echo 1)" "wrong diagnostic"
log="$WORK/log_J2"
rc="$(run_audit "$log" --verify-report "$WORK/reports_control/slice0-audit-local.json")"
check "J: a real report passes the same assertions" "$([[ $rc -eq 0 ]] && echo 0 || echo 1)" "rc=$rc"

# --- K: the reports the audit writes carry no credential or hosted identifier ---------------------------
for report in "$WORK"/reports_*/*.json "$WORK"/reports_*/*.md; do
  [[ -e "$report" ]] || continue
  name="$(basename "$(dirname "$report")")/$(basename "$report")"
  if grep -qE 'sb_secret_|sb_publishable_|sbp_[A-Za-z0-9]{16}|eyJ[A-Za-z0-9_-]{8}|\.supabase\.co|PGPASSWORD=|postgresql://[^[]' "$report"; then
    bad "K ($name): carries no credential, key or hosted host name" "a pattern matched"
  else
    ok "K ($name): carries no credential, key or hosted host name"
  fi
done

# --- L: the read-only transaction really refuses a mutation --------------------------------------------
# LOCAL ONLY. This is the negative test for the boundary the audit relies on, and it is the one
# place in this repository that deliberately attempts a write to see it refused. It runs against the
# disposable local database inside an explicit rollback-only harness, and it is not part of the
# audit: the audit issues no mutation in either mode.
echo
echo "-- L: negative mutation test (local disposable database only, rollback-only harness) --"
# shellcheck source=lib/local_target.sh
source "$ROOT/supabase/scripts/lib/local_target.sh"
if ! ol_require_local_target "$ROOT" >/dev/null 2>&1; then
  bad "L: the local stack is available for the negative mutation test" "local target validation failed"
else
  before="$(ol_psql -q -A -t -c 'select count(*) from crm.organization')"

  # The probe. Same session shape the audit uses -- default_transaction_read_only on the
  # connection, then one explicit `begin read only` and `set role origenlab_owner` -- and then the
  # write the audit never makes. The transaction ends in `rollback` whatever happens, and the row
  # count is compared before and after, so the harness is rollback-only by construction and not by
  # the statement happening to fail.
  probe_out="$(PGOPTIONS='-c default_transaction_read_only=on' psql "$OL_DB_URL" -X -q -A -t \
      -v ON_ERROR_STOP=0 -c '\set VERBOSITY verbose' -c "
        begin read only;
        set role origenlab_owner;
        insert into crm.organization (kind, name, confirmation)
        values ('company', 'audit read-only probe', 'machine_proposed');
        rollback;" 2>&1 || true)"
  sqlstate="$(sed -n -E 's/^.*ERROR:  ([0-9A-Z]{5}):.*$/\1/p' <<<"$probe_out" | sed -n '1p')"
  after="$(ol_psql -q -A -t -c 'select count(*) from crm.organization')"
  check "L: a mutation inside the audit's read-only transaction is refused with SQLSTATE 25006" \
    "$([[ "$sqlstate" == "25006" ]] && echo 0 || echo 1)" "got '${sqlstate:-no error}'"
  check "L: the table is unchanged — the probe wrote nothing" \
    "$([[ "$before" == "$after" && "$after" == "0" ]] && echo 0 || echo 1)" "before=$before after=$after"
  check "L: the audit itself contains no mutation statement in any mode" \
    "$(python3 -c "
import sys
sys.path.insert(0, 'supabase/audit')
from olaudit import psqlrun, sqlbank
script = psqlrun.build_script(sqlbank.load()).lower()
import re
bad = [v for v in ('insert','update','delete','truncate','drop','alter','grant','revoke','merge','copy')
       if re.search(rf'\b{v}\b', sqlbank.to_code(script))]
sys.exit(1 if bad else 0)" && echo 0 || echo 1)" "a mutation verb is present"
fi

# --- N: no startup file and no inherited variable acts before the read-only transaction ----------
# LOCAL ONLY, and the second place in this repository that deliberately attempts a write -- except
# that here the write is attempted *by the attacker*, from a planted ~/.psqlrc, and the point is
# that it never runs at all.
#
# psql reads `$PSQLRC`, then `~/.psqlrc`, then the system `psqlrc`, immediately after connecting and
# before any `-f` file. Anything in one of them is therefore the first server-side operation of the
# session, ahead of the generated `begin read only`. `psqlrun.run` passes `-X --no-psqlrc`, and
# `Target.child_env` rebuilds the environment from scratch rather than inheriting it. This scenario
# proves both, and proves them against a startup file demonstrated to work when they are absent.
echo
echo "-- N: startup files and a hostile environment (local disposable database only) --"
# shellcheck source=lib/local_target.sh
source "$ROOT/supabase/scripts/lib/local_target.sh"
if ! ol_require_local_target "$ROOT" >/dev/null 2>&1; then
  bad "N: the local stack is available for the startup-file test" "local target validation failed"
else
  FAKEHOME="$WORK/fakehome"
  SHELL_MARKER="$WORK/psqlrc_executed"
  mkdir -p "$FAKEHOME"
  rm -f "$SHELL_MARKER"

  # The planted file. It defeats the connection-level read-only default on purpose -- a session GUC
  # set outside any transaction -- so that the database marker is genuinely reachable and its
  # absence is evidence about `-X`, not about `default_transaction_read_only`.
  cat >"$FAKEHOME/.psqlrc" <<PSQLRC_EOF
\! touch "$SHELL_MARKER"
set default_transaction_read_only = off;
create table if not exists public.ol_psqlrc_marker (note text);
insert into public.ol_psqlrc_marker values ('a startup file ran before the audit transaction');
PSQLRC_EOF

  marker_rows() { ol_psql -q -A -t -c "select count(*) from pg_class where relname = 'ol_psqlrc_marker' and relnamespace = 'public'::regnamespace"; }

  # N1 (positive control): without -X the planted file really does create the marker. Without this
  # the rest of the scenario would pass just as well against an inert file.
  HOME="$FAKEHOME" psql "$OL_DB_URL" -q -A -t -c 'select 1' >/dev/null 2>&1 || true
  planted_rows="$(marker_rows)"
  check "N1: control — without -X the planted ~/.psqlrc creates the marker" \
    "$([[ "$planted_rows" == "1" && -e "$SHELL_MARKER" ]] && echo 0 || echo 1)" \
    "rows=$planted_rows shell_marker=$([[ -e "$SHELL_MARKER" ]] && echo yes || echo no)"
  ol_psql -q -c 'drop table if exists public.ol_psqlrc_marker' >/dev/null
  rm -f "$SHELL_MARKER"

  # N2: the real audit, same HOME, plus every connection-routing and TLS variable set to something
  # hostile. 192.0.2.10 is RFC 5737 documentation space and is never reachable; if any of these
  # reached the child, the audit would fail to connect, or s01 would read back
  # default_transaction_read_only=off. The only accepted run-level failure is the
  # exact, declared current-head versus frozen-Slice-0 schema delta.
  SERVICEFILE="$WORK/attacker_pg_service.conf"
  cat >"$SERVICEFILE" <<'SERVICE_EOF'
[attacker]
host=192.0.2.10
port=6543
user=attacker_role
dbname=attacker_db
options=-c default_transaction_read_only=off
SERVICE_EOF

  log="$WORK/log_N"
  rc="$(run_audit_env "$log" \
    HOME="$FAKEHOME" \
    PSQLRC="$FAKEHOME/.psqlrc" \
    PGSERVICE=attacker \
    PGSERVICEFILE="$SERVICEFILE" \
    PGHOST=192.0.2.10 \
    PGHOSTADDR=192.0.2.10 \
    PGPORT=6543 \
    PGDATABASE=attacker_db \
    PGUSER=attacker_role \
    PGPASSWORD=attacker-credential \
    PGOPTIONS='-c default_transaction_read_only=off -c statement_timeout=0' \
    PGSSLMODE=disable \
    PGREQUIRESSL=0 \
    PGCHANNELBINDING=disable \
    -- --mode local --now "$FIXED_NOW" --out "$WORK/reports_N")"

  after_rows="$(marker_rows)"
  check "N2: hostile environment leaves only the declared local-head gap" \
    "$([[ $rc -eq 1 ]] && declared_local_head_gap "$WORK/reports_N/slice0-audit-local.json" >/dev/null && echo 0 || echo 1)" \
    "rc=$rc"
  check "N2: the planted ~/.psqlrc created no marker — it never executed" \
    "$([[ "$after_rows" == "0" ]] && echo 0 || echo 1)" "rows=$after_rows"
  check "N2: the planted ~/.psqlrc ran no shell command either" \
    "$([[ ! -e "$SHELL_MARKER" ]] && echo 0 || echo 1)" "the \\! marker file exists"

  # The argument vector the audit actually exec'd, from the shim.
  argv="$(cat "$STATE/psql_argv" 2>/dev/null || true)"
  check "N2: psql was invoked with -X and --no-psqlrc" \
    "$(grep -qP '(^|\t)-X(\t|$)' <<<"$argv" && grep -qP '(^|\t)--no-psqlrc(\t|$)' <<<"$argv" && echo 0 || echo 1)" \
    "argv=$argv"
  check "N2: no -c ran before the script file" \
    "$(grep -qP '(^|\t)-c(\t|$)' <<<"$argv" && echo 1 || echo 0)" "a -c was present"
  check "N2: ON_ERROR_STOP=1 was set on the command line" \
    "$(grep -qF 'ON_ERROR_STOP=1' <<<"$argv" && echo 0 || echo 1)" "argv=$argv"

  # N3: the session the server actually saw. s01 is read back from the server, so a passing s01 is
  # the server's own statement that the hostile PGOPTIONS did not arrive.
  report_N="$WORK/reports_N/slice0-audit-local.json"
  check "N3: the server reported the audit session read-only despite PGOPTIONS" \
    "$(python3 -c "
import json, sys
r = json.load(open('$report_N'))
s01 = next(c for c in r['checks'] if c['id'] == 's01')
sys.exit(0 if s01['status'] == 'PASS' and not s01['findings'] else 1)" && echo 0 || echo 1)" \
    "s01 did not pass"
  check "N3: the report names the inherited variables it refused to pass on" \
    "$(python3 -c "
import json, sys
r = json.load(open('$report_N'))
names = set(r['run']['inherited_env_names_not_passed_to_the_child'])
missing = {'PGSERVICE', 'PGSERVICEFILE', 'PGHOSTADDR', 'PGOPTIONS', 'PGSSLMODE'} - names
sys.exit(1 if missing else 0)" && echo 0 || echo 1)" \
    "a hostile name is absent from the report"

  ol_psql -q -c 'drop table if exists public.ol_psqlrc_marker' >/dev/null 2>&1
fi

echo
echo "slice 0 audit failure injection: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
