#!/usr/bin/env bash
# OrigenLab V2 — failure injection for the hosted role bootstrap.
#
# The bootstrap is the file that will one day create roles on a hosted project, so it is tested the
# way the rest of the evidence tooling is: by making it fail on purpose and proving it says so, and
# proving that when it refuses it emits no SQL at all.
#
# Every scenario runs the real supabase/scripts/hosted_role_bootstrap.sh. Scenarios that need a
# malformed bootstrap file plant one over supabase/hosted_roles.sql — the same plant-and-restore
# pattern audit_failure_tests.sh uses for the SQL bank — and the suite ends by proving the file is
# byte-identical to where it started.
#
# Restoration is from a byte copy taken before the first plant, never from `git checkout`. That
# matters: a git restore silently does nothing when the file is untracked, which would leave a
# planted credential in the working tree for someone to commit by accident. Copying back always
# works, and every restore is verified with `cmp` rather than assumed.
#
# NOTHING HERE CONTACTS A DATABASE, LOCAL OR HOSTED, AND NEITHER DOES THE TOOL UNDER TEST. `psql`
# and `supabase` are shimmed onto the front of PATH purely so the suite can prove they were never
# invoked, by any scenario, at all. This suite therefore needs no running stack and no Docker: it
# requires bash, git and python3, and installs nothing.
#
# Procedure: docs/OPERATIONS.md §4.3.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

BOOTSTRAP="$ROOT/supabase/scripts/hosted_role_bootstrap.sh"
TARGET="supabase/hosted_roles.sql"

command -v git >/dev/null 2>&1 || { echo "FAIL: git is required" >&2; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "FAIL: python3 is required" >&2; exit 2; }

WORK="$(mktemp -d)"
PRISTINE="$WORK/hosted_roles.sql.pristine"
cp "$ROOT/$TARGET" "$PRISTINE"

restore() {
  # Unconditional, and verified. Runs on every exit path including an interrupt or a set -e abort.
  if ! cmp -s "$PRISTINE" "$ROOT/$TARGET"; then
    cp "$PRISTINE" "$ROOT/$TARGET"
    echo "restored $TARGET from the pristine copy taken at start" >&2
  fi
  cmp -s "$PRISTINE" "$ROOT/$TARGET" || {
    echo "FATAL: could not restore $TARGET; it may still hold a planted fixture" >&2
    exit 3
  }
  if [[ -n "${OL_KEEP_WORK:-}" ]]; then
    echo "work dir kept: $WORK"
  else
    rm -rf "$WORK"
  fi
}
trap restore EXIT INT TERM

SHIM="$WORK/shim"; STATE="$WORK/state"
mkdir -p "$SHIM" "$STATE"
for tool in psql supabase pg_dump; do
  cat >"$SHIM/$tool" <<SHIM_EOF
#!/usr/bin/env bash
printf '%s\n' "$tool" >>"$STATE/tool_calls"
exit 97
SHIM_EOF
  chmod +x "$SHIM/$tool"
done

# The tree may legitimately carry unrelated modifications when this suite is run by hand. What
# scenario P proves is that *this suite* changed nothing, so the baseline is taken now.
DIRTY_BEFORE="$(git -C "$ROOT" diff --name-only | sort)"

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf 'ok    %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf 'not ok %s  [%s]\n' "$1" "${2:-}"; }
check() { if [[ "$2" == 0 ]]; then ok "$1"; else bad "$1" "${3:-}"; fi }

OUT=""; ERR=""; RC=0
run() { # remaining args passed to the real entry point
  OUT="$WORK/out"; ERR="$WORK/err"; RC=0
  env PATH="$SHIM:$PATH" "$BOOTSTRAP" "$@" >"$OUT" 2>"$ERR" || RC=$?
}

plant() { # $1 = python expression producing the tampered text from the pristine original
  # Always derived from the pristine copy, so a scenario cannot accidentally build on the previous
  # scenario's plant, and a transformation that changes nothing is a hard error rather than a
  # scenario that passes for the wrong reason.
  python3 - "$PRISTINE" "$ROOT/$TARGET" "$1" <<'PY'
import pathlib, sys
original = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
tampered = eval(sys.argv[3], {"original": original})
if tampered == original:
    sys.exit("plant expression changed nothing; the scenario would prove nothing")
pathlib.Path(sys.argv[2]).write_text(tampered, encoding="utf-8")
PY
}

unplant() {
  cp "$PRISTINE" "$ROOT/$TARGET"
  cmp -s "$PRISTINE" "$ROOT/$TARGET" || { echo "FATAL: restore failed" >&2; exit 3; }
}

# A refusal must produce a non-zero status, an empty stdout, and a diagnostic naming the reason.
refused() { # $1 = label, $2 = substring expected in stderr
  check "$1: exits non-zero" "$([[ $RC -ne 0 ]] && echo 0 || echo 1)" "rc=$RC"
  check "$1: emits no SQL on stdout" "$([[ ! -s "$OUT" ]] && echo 0 || echo 1)" "stdout was not empty"
  check "$1: the refusal names the reason" \
    "$(grep -qi -- "$2" "$ERR" && echo 0 || echo 1)" "stderr did not mention '$2'"
}

echo "== hosted role bootstrap: failure injection =="
echo

# --- control: the real dry run succeeds and emits exactly the committed file ----------------------
run --environment staging --dry-run
check "control: a staging dry run exits 0" "$([[ $RC -eq 0 ]] && echo 0 || echo 1)" "rc=$RC"
check "control: stdout is byte-identical to the committed bootstrap file" \
  "$(cmp -s "$ROOT/$TARGET" "$OUT" && echo 0 || echo 1)" "stdout differs from $TARGET"
check "control: the summary reports the role matrix" \
  "$(grep -q 'origenlab_migrator' "$ERR" && grep -q 'NOINHERIT' "$ERR" && echo 0 || echo 1)" "no matrix"
check "control: the summary records the staging durability posture" \
  "$(grep -qi 'seven-day' "$ERR" && grep -qi 'declined' "$ERR" && echo 0 || echo 1)" "no posture"

# The disclosure rule, asserted against the bytes an operator would pipe or paste.
for forbidden in 'supabase.co' 'postgresql://' 'postgres://' 'sslmode' 'PGPASSWORD' 'staging'; do
  check "control: stdout discloses no '$forbidden'" \
    "$(grep -qi -- "$forbidden" "$OUT" && echo 1 || echo 0)" "found $forbidden in the emitted SQL"
done
check "control: stderr discloses no host name, project reference or connection string" \
  "$(grep -qEi 'supabase\.co|postgres(ql)?://|[a-z]{20}\.supabase|PGPASSWORD' "$ERR" && echo 1 || echo 0)" \
  "a target identifier reached the summary"

# --- A: a password in the bootstrap file ---------------------------------------------------------
plant "original.replace('alter role origenlab_migrator login   noinherit nocreatedb nocreaterole;', \"alter role origenlab_migrator login noinherit password 'hunter2';\")"
run --environment staging --dry-run
refused "A (a password in the bootstrap file)" "password"
unplant

# --- B: a membership granted to a Supabase-managed role ------------------------------------------
plant "original + '\ngrant origenlab_owner to postgres;\n'"
run --environment staging --dry-run
refused "B (owner granted to the platform postgres role)" "supabase-managed"
unplant

# --- C: altering a Supabase-managed role ---------------------------------------------------------
plant "original + '\nalter role service_role nocreatedb;\n'"
run --environment staging --dry-run
refused "C (a Supabase-managed role altered)" "supabase-managed"
unplant

# --- D: a fifth OrigenLab role -------------------------------------------------------------------
plant "original + '\ncreate role origenlab_readonly nologin nosuperuser nobypassrls noreplication;\n'"
run --environment staging --dry-run
refused "D (a fifth role)" "closed OrigenLab role set"
unplant

# --- E: a second membership in the owner ---------------------------------------------------------
plant "original + '\ngrant origenlab_owner to origenlab_api;\n'"
run --environment staging --dry-run
refused "E (a runtime role granted the owner)" "exactly one membership"
unplant

# --- F: the owner membership made inheriting -----------------------------------------------------
plant "original.replace('with inherit false, set true, admin false', 'with inherit true, set true, admin false')"
run --environment staging --dry-run
refused "F (an inheriting owner membership)" "INHERIT FALSE"
unplant

# --- G: a privileged attribute -------------------------------------------------------------------
plant "original.replace('nobypassrls', 'bypassrls', 1)"
run --environment staging --dry-run
refused "G (a BYPASSRLS attribute)" "bypassrls"
unplant

# --- H: a dropped role ---------------------------------------------------------------------------
plant "original + '\ndrop role origenlab_worker;\n'"
run --environment staging --dry-run
refused "H (a dropped role)" "drop"
unplant

# --- I: a statement shape the analyser does not understand ---------------------------------------
# The completeness rule. Without it this statement would simply not be matched, and not matched is
# exactly what "silently permitted" looks like.
plant "original + '\ngrant origenlab_api, origenlab_worker to origenlab_migrator;\n'"
run --environment staging --dry-run
refused "I (an unrecognised statement shape)" "analyser does not understand"
unplant

# --- J: a psql meta-command in the file ----------------------------------------------------------
plant "original + '\n\\\\\\\\password origenlab_migrator\n'"
run --environment staging --dry-run
refused "J (a psql meta-command)" "meta-command"
unplant

# --- K: DDL beyond role management ---------------------------------------------------------------
plant "original + '\ncreate table crm.sneaky (id int);\n'"
run --environment staging --dry-run
refused "K (a table created by the role bootstrap)" "forbidden token"
unplant

# --- L: production is blocked on its own durability decision --------------------------------------
run --environment production --dry-run
refused "L (production)" "blocked"
check "L: the refusal names the missing RPO/PITR decision" \
  "$(grep -qi 'pitr' "$ERR" && grep -qi 'recovery-point' "$ERR" && echo 0 || echo 1)" "wrong diagnostic"
check "L: staging's posture is not offered as a substitute for production's" \
  "$(grep -qi 'not a precedent' "$ERR" && echo 0 || echo 1)" "no precedent warning"

# --- M: the environment classification is required, closed, and never echoed ----------------------
run --dry-run
check "M: a missing --environment refuses" "$([[ $RC -ne 0 ]] && echo 0 || echo 1)" "rc=$RC"
check "M: a missing --environment emits no SQL" "$([[ ! -s "$OUT" ]] && echo 0 || echo 1)" "stdout not empty"

run --environment staging
check "M: a missing --dry-run refuses, so the absence of an apply mode stays explicit" \
  "$([[ $RC -ne 0 ]] && echo 0 || echo 1)" "rc=$RC"

run --environment dev --dry-run
refused "M (an unapproved classification)" "not an approved environment classification"

SECRETISH='postgresql://origenlab_migrator:hunter2@db.abcdefghijklmnopqrst.supabase.co:5432/postgres'
run --environment "$SECRETISH" --dry-run
check "M: a connection string as the classification refuses" "$([[ $RC -ne 0 ]] && echo 0 || echo 1)" "rc=$RC"
check "M: the refusal does not echo the connection string back" \
  "$(grep -qF -- "$SECRETISH" "$ERR" && echo 1 || echo 0)" "the value was echoed"
check "M: the refusal does not echo the host name or the project reference" \
  "$(grep -qEi 'supabase\.co|abcdefghijklmnopqrst|hunter2' "$ERR" && echo 1 || echo 0)" "an identifier leaked"

# --- N: there is no apply mode, and no way to name a target ---------------------------------------
for flag in --apply --execute --host --target --connection-string --password --project-ref; do
  run --environment staging --dry-run "$flag" x
  check "N: $flag is not a recognised argument" "$([[ $RC -ne 0 ]] && echo 0 || echo 1)" "rc=$RC"
done

# --- O: the tool cannot connect, and never did ----------------------------------------------------
check "O: no scenario invoked psql, supabase or pg_dump" \
  "$([[ ! -s "$STATE/tool_calls" ]] && echo 0 || echo 1)" "$(cat "$STATE/tool_calls" 2>/dev/null | sort -u | tr '\n' ' ')"
check "O: neither bootstrap module can reach a database" \
  "$(python3 - <<'PY'
import pathlib, sys
bad = []
for name in ("bootstrap.py", "bootstrap_cli.py"):
    text = pathlib.Path("supabase/audit/olaudit", name).read_text(encoding="utf-8")
    bad += [t for t in ("import socket", "import subprocess", "psqlrun", "target_hosted", "target_local") if t in text]
sys.exit(1 if bad else 0)
PY
  echo 0 || echo 1)" "a connection capability is importable"

# --- P: the tracked bootstrap file survived the suite unchanged -----------------------------------
check "P: supabase/hosted_roles.sql is byte-identical to its pre-suite bytes" \
  "$(cmp -s "$PRISTINE" "$ROOT/$TARGET" && echo 0 || echo 1)" "the planted file was not restored"
check "P: supabase/hosted_roles.sql holds no planted credential" \
  "$(grep -qiE "^[^-]*\\bpassword\\b" "$ROOT/$TARGET" && echo 1 || echo 0)" "a password survived a plant"
check "P: this suite modified no tracked file" \
  "$([[ "$(git -C "$ROOT" diff --name-only | sort)" == "$DIRTY_BEFORE" ]] && echo 0 || echo 1)" \
  "$(comm -13 <(printf '%s\n' "$DIRTY_BEFORE") <(git -C "$ROOT" diff --name-only | sort) | tr '\n' ' ')"

echo
echo "hosted role bootstrap failure injection: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
