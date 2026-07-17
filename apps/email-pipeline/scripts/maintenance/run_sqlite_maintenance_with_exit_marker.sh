#!/usr/bin/env bash
# Robust offline SQLite compaction / audit wrapper with durable exit markers.
#
# Always records the real numeric exit code of the wrapped command, including 0.
# Avoids nested systemd quoting pitfalls that previously produced blank exit markers.
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
      sed -n '1,20p' "$0"
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

set +e
"$@" >>"${PROGRESS_LOG}" 2>&1
rc=$?
set -e

# Always write a numeric exit code (including 0). Never leave a blank value.
printf 'AUDIT_RESUME_EXIT=%s\n' "${rc}" >> "${PROGRESS_LOG}"
printf '%s\n' "${rc}" > "${EXIT_MARKER}"

exit "${rc}"
