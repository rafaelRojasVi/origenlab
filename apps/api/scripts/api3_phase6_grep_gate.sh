#!/usr/bin/env bash
# API-3 Phase 6 zero-reference gate (grep audit).
#
# Scans the repo for legacy :8000 and legacy route-family references.
# Exits 1 when matches exist outside api3_phase6_grep_allowlist.txt.
#
# NOT required to pass during Phases 4A–5 (expected to fail until Phase 6).
# Usage:
#   apps/api/scripts/api3_phase6_grep_gate.sh
#   API3_PHASE6_GATE_WARN_ONLY=1 apps/api/scripts/api3_phase6_grep_gate.sh  # exit 0, print hits
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ALLOWLIST="${SCRIPT_DIR}/api3_phase6_grep_allowlist.txt"
WARN_ONLY="${API3_PHASE6_GATE_WARN_ONLY:-0}"

if [[ ! -f "${ALLOWLIST}" ]]; then
  echo "ERROR: missing allowlist ${ALLOWLIST}" >&2
  exit 2
fi

if ! command -v rg >/dev/null 2>&1; then
  echo "ERROR: ripgrep (rg) required for Phase 6 gate" >&2
  exit 2
fi

RG_OPTS=(
  --no-heading
  --line-number
  --glob '!.venv/**'
  --glob '!**/.venv/**'
  --glob '!**/node_modules/**'
  --glob '!**/dist/**'
  --glob '!.git/**'
  --glob '!**/uv.lock'
  --glob '!**/package-lock.json'
  --glob '!**/pnpm-lock.yaml'
)

# Legacy host references
PATTERNS=(
  '127\.0\.0\.1:8000'
  'localhost:8000'
  'port 8000'
  'port :8000'
)

# Legacy route families (mirror twins live under /mirror/* on :8001)
ROUTE_PATTERNS=(
  '/dashboard/summary'
  '/classification/'
  '/commercial/purchase-events'
  '/meta/dashboard-sync'
  '/outbound/'
  'smoke:legacy'
  'legacy-smoke'
)

# Loaded once: re-reading the file through `echo | tr` for every hit cost ~14 s over ~340 hits.
ALLOW_PREFIXES=()
while IFS= read -r line || [[ -n "${line}" ]]; do
  line="${line%%#*}"
  line="${line//[[:space:]]/}"
  [[ -n "${line}" ]] && ALLOW_PREFIXES+=("${line}")
done < "${ALLOWLIST}"

is_allowlisted() {
  local rel="$1" prefix
  for prefix in "${ALLOW_PREFIXES[@]}"; do
    [[ "${rel}" == "${prefix}"* ]] && return 0
  done
  return 1
}

collect_hits() {
  local pat="$1"
  rg "${RG_OPTS[@]}" --regexp "${pat}" "${ROOT}" 2>/dev/null || true
}

declare -A SEEN=()
VIOLATIONS=()

record_violation() {
  local rel="$1"
  local line_no="$2"
  local pat="$3"
  local text="$4"
  local key="${rel}:${line_no}:${pat}"
  [[ -n "${SEEN[$key]:-}" ]] && return
  SEEN[$key]=1
  VIOLATIONS+=("${rel}:${line_no}: [${pat}] ${text}")
}

# Route-family patterns also match /mirror/* twins; negative tests cite removed refs.
should_skip_hit() {
  local pat="$1"
  local text="$2"
  if [[ "${text}" == *"/mirror/"* ]]; then
    return 0
  fi
  if [[ "${text}" == *"not in text"* || "${text}" == *"not in pkg"* || "${text}" == *"removed"* ]]; then
    return 0
  fi
  if [[ "${text}" == *"assert not"* || "${text}" == *"re.compile"* || "${text}" == *"re.search"* || "${text}" == *"FORBIDDEN_LEGACY"* ]]; then
    return 0
  fi
  if [[ "${text}" == *"/mirror/"* && "${text}" == *"${pat}"* ]]; then
    return 0
  fi
  if [[ "${pat}" == "/dashboard/summary" && "${text}" == *"mirror/dashboard"* ]]; then
    return 0
  fi
  if [[ "${pat}" == "/outbound/" && "${text}" == *"core/outbound"* ]]; then
    return 0
  fi
  if [[ "${pat}" == "smoke:legacy" && "${text}" == *"assert"* ]]; then
    return 0
  fi
  if [[ "${pat}" == "legacy-smoke" && "${text}" == *"LEGACY_SMOKE"* ]]; then
    return 0
  fi
  if [[ "${pat}" == "/classification/" && "${text}" == *"must not call"* ]]; then
    return 0
  fi
  if [[ "${pat}" == "/dashboard/summary" && "${text}" == *"parity"* ]]; then
    return 0
  fi
  if [[ "${pat}" == '"/contacts"' && "${text}" == *"contacts"* ]]; then
    return 0
  fi
  if [[ "${pat}" == "/mirror/contacts" ]]; then
    return 0
  fi
  return 1
}

while IFS= read -r pat; do
  while IFS= read -r row; do
    [[ -z "${row}" ]] && continue
    rel="${row%%:*}"
    rest="${row#*:}"
    line_no="${rest%%:*}"
    text="${rest#*:}"
    text="${text# }"
    rel="${rel#"${ROOT}/"}"
    rel="${rel#./}"
    if is_allowlisted "${rel}"; then
      continue
    fi
    if should_skip_hit "${pat}" "${text}"; then
      continue
    fi
    record_violation "${rel}" "${line_no}" "${pat}" "${text}"
  done < <(collect_hits "${pat}")
done < <(printf '%s\n' "${PATTERNS[@]}" "${ROUTE_PATTERNS[@]}")

# Mart list routes: narrow patterns to reduce false positives on /contacts/{email}
LIST_PATTERNS=(
  'apiUrl\("/contacts'
  'apiUrl\("/organizations'
  'GET /contacts"'
  'GET /organizations'
  '"/contacts"'
  '/mirror/contacts'
)
while IFS= read -r pat; do
  while IFS= read -r row; do
    [[ -z "${row}" ]] && continue
    rel="${row%%:*}"
    rest="${row#*:}"
    line_no="${rest%%:*}"
    text="${rest#*:}"
    text="${text# }"
    rel="${rel#"${ROOT}/"}"
    rel="${rel#./}"
    if is_allowlisted "${rel}"; then
      continue
    fi
    # Operator detail uses /contacts/{email} — allow only if not mart-list legacy client
    if [[ "${pat}" == "\"/contacts\"" && "${rel}" == *operatorClient* ]]; then
      continue
    fi
    if should_skip_hit "${pat}" "${text}"; then
      continue
    fi
    record_violation "${rel}" "${line_no}" "${pat}" "${text}"
  done < <(collect_hits "${pat}")
done < <(printf '%s\n' "${LIST_PATTERNS[@]}")

echo "== API-3 Phase 6 grep gate =="
echo "Repo: ${ROOT}"
echo "Allowlist: ${ALLOWLIST}"
echo "Unallowlisted hits: ${#VIOLATIONS[@]}"

if [[ ${#VIOLATIONS[@]} -eq 0 ]]; then
  echo "OK: no legacy references outside allowlist (Phase 6 gate would pass)."
  exit 0
fi

echo "Legacy references outside allowlist (expected until Phase 6):"
for v in "${VIOLATIONS[@]}"; do
  echo "  ${v}"
done

if [[ "${WARN_ONLY}" == "1" ]]; then
  echo "WARN_ONLY=1: exiting 0 (gate not enforced)."
  exit 0
fi

echo "FAIL: update allowlist only for intentional deprecated/parked/test references." >&2
exit 1
