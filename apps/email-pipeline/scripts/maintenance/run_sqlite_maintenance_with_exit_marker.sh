#!/usr/bin/env bash
# Robust offline SQLite compaction / audit wrapper with durable exit markers.
#
# Always records the real numeric exit code of the wrapped command, including 0.
# Avoids nested systemd quoting pitfalls that previously produced blank exit markers
# (for example `exit ""` from an empty expanded variable).
#
# Usage:
#   run_sqlite_maintenance_with_exit_marker.sh \
#     --progress-log /path/to/progress.log \
#     --exit-marker /path/to/exit.marker \
#     -- command arg1 arg2 ...
#
# Environment:
#   ORIGENLAB_MAINTENANCE_PROGRESS_LOG  optional default progress log
#   ORIGENLAB_MAINTENANCE_EXIT_MARKER   optional default exit marker path
#
# Markers written to the progress log (both, for compatibility):
#   SQLITE_MAINTENANCE_EXIT=<integer>
#   AUDIT_RESUME_EXIT=<integer>
set -uo pipefail

PROGRESS_LOG="${ORIGENLAB_MAINTENANCE_PROGRESS_LOG:-}"
EXIT_MARKER="${ORIGENLAB_MAINTENANCE_EXIT_MARKER:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --progress-log)
      PROGRESS_LOG="${2:-}"
      shift 2
      ;;
    --exit-marker)
      EXIT_MARKER="${2:-}"
      shift 2
      ;;
    --)
      shift
      break
      ;;
    -h|--help)
      sed -n '1,30p' "$0"
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ $# -lt 1 ]]; then
  echo "ERROR: missing command after --" >&2
  exit 2
fi
if [[ -z "${PROGRESS_LOG}" ]]; then
  echo "ERROR: --progress-log is required (or ORIGENLAB_MAINTENANCE_PROGRESS_LOG)" >&2
  exit 2
fi
if [[ -z "${EXIT_MARKER}" ]]; then
  echo "ERROR: --exit-marker is required (or ORIGENLAB_MAINTENANCE_EXIT_MARKER)" >&2
  exit 2
fi

mkdir -p "$(dirname "${PROGRESS_LOG}")" "$(dirname "${EXIT_MARKER}")"

{
  echo "MAINTENANCE_START_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "MAINTENANCE_CMD=$*"
} >> "${PROGRESS_LOG}"

# Default until the wrapped command returns a code.
rc=1
marker_written=0

normalize_rc() {
  local value="${1-}"
  case "${value}" in
    ''|*[!0-9]*)
      printf '%s\n' "1"
      ;;
    *)
      printf '%s\n' "${value}"
      ;;
  esac
}

write_exit_marker() {
  local code
  code="$(normalize_rc "${1-}")"
  # Always write a numeric exit code (including 0). Never leave a blank value.
  printf 'SQLITE_MAINTENANCE_EXIT=%s\n' "${code}" >> "${PROGRESS_LOG}"
  # Backward-compatible alias used by older audit resume docs.
  printf 'AUDIT_RESUME_EXIT=%s\n' "${code}" >> "${PROGRESS_LOG}"
  printf '%s\n' "${code}" > "${EXIT_MARKER}"
  marker_written=1
  rc="${code}"
}

on_signal() {
  local sig="${1}"
  # Conventional 128+signal when the wrapper itself is interrupted.
  write_exit_marker "$((128 + sig))"
  # Disable EXIT trap to avoid double-write with a stale rc.
  trap - EXIT
  exit "${rc}"
}

on_exit() {
  if [[ "${marker_written}" -eq 0 ]]; then
    write_exit_marker "${rc}"
  fi
}

trap 'on_signal 15' TERM
trap 'on_signal 2' INT
trap 'on_exit' EXIT

# Intentionally NOT set -e: the wrapped command must be allowed to fail so we
# still write a marker for ordinary nonzero exits.
set +e
"$@" >>"${PROGRESS_LOG}" 2>&1
rc=$?
set +e

write_exit_marker "${rc}"
trap - EXIT
exit "${rc}"
