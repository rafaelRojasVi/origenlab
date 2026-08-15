#!/usr/bin/env bash
# W1 institution-prospect bundle → Render persistent disk (staged, atomic).
#
# Transfers an already-published local W1 canonical bundle to the Render API's
# persistent disk (/var/data) using scp + ssh. The API never reads a partially
# uploaded bundle: extraction and Python validation happen in a versioned staging
# directory; only a validated snapshot atomically replaces the canonical symlink.
#
# ATOMICITY MODEL:
#   Remote staging dir: /var/data/institution_prospects.TIMESTAMP.staging/
#   After remote Python validation succeeds:
#     1. ln -s STAGING /var/data/institution_prospects.next
#     2. mv /var/data/institution_prospects.next /var/data/institution_prospects
#   Step 2 is a single rename(2) syscall — atomic on the same filesystem.
#   A concurrent API request sees either the old or new complete bundle, never
#   a partial one.  The API's resolved_institution_prospect_dir() follows the
#   symlink on every request, so no restart is required after promotion.
#
# FAILURE BEHAVIOUR:
#   - Missing / invalid local bundle → exit 2 before any network I/O.
#   - Failed SCP upload or remote extraction → staging dir cleaned, canonical
#     bundle untouched, exit 2.
#   - Failed remote Python validation → staging dir cleaned, canonical bundle
#     untouched, exit 2.
#   - Failed symlink promotion → canonical bundle untouched, staging dir left
#     for inspection, exit 2.
#
# SAFETY:
#   - No SQLite writes, Gmail, outreach, or W1 regeneration.
#   - SSH private key is specified by PATH only; contents are never printed.
#   - No --delete against an unvalidated canonical target.
#   - No database writes.
#
# Required env:
#   ORIGENLAB_RENDER_SSH_HOST   Render SSH hostname (e.g. ssh.oregon.render.com)
#   ORIGENLAB_RENDER_SSH_USER   Render SSH username for the service (srv-*)
#   ORIGENLAB_RENDER_SSH_KEY    Filesystem path to SSH private key (not contents)
#
# Optional env:
#   ORIGENLAB_W1_LOCAL_DIR        Local W1 source directory
#                                  (default: reports/out/active/current/institution_prospects)
#   ORIGENLAB_W1_REMOTE_DATA      Remote persistent data root on disk
#                                  (default: /var/data)
#   ORIGENLAB_W1_KEEP_SNAPSHOTS   Old remote snapshots to retain after promotion
#                                  (default: 2; must be >= 1)
#
# See docs/PHASE1_CLOUD_READ_PATH.md §4 — W1 institution-prospect sync.
set -euo pipefail

PIPE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# shellcheck source=scripts/ops/_load_pipeline_dotenv.sh
source "${PIPE}/scripts/ops/_load_pipeline_dotenv.sh"
load_pipeline_dotenv "$PIPE"

# ---------------------------------------------------------------------------
# Configuration (env-overridable, never secret)
# ---------------------------------------------------------------------------
W1_LOCAL_DIR="${ORIGENLAB_W1_LOCAL_DIR:-${PIPE}/reports/out/active/current/institution_prospects}"
W1_REMOTE_DATA="${ORIGENLAB_W1_REMOTE_DATA:-/var/data}"
W1_KEEP_SNAPSHOTS="${ORIGENLAB_W1_KEEP_SNAPSHOTS:-2}"
W1_CANONICAL="${W1_REMOTE_DATA}/institution_prospects"

# Validate W1_KEEP_SNAPSHOTS before any network I/O.
if ! [[ "${W1_KEEP_SNAPSHOTS}" =~ ^[0-9]+$ ]] || [[ "${W1_KEEP_SNAPSHOTS}" -lt 1 ]]; then
  echo "ERROR: ORIGENLAB_W1_KEEP_SNAPSHOTS must be a decimal integer >= 1 (got: '${W1_KEEP_SNAPSHOTS}')." >&2
  exit 2
fi

# SSH credentials — required, values never echoed.
: "${ORIGENLAB_RENDER_SSH_HOST:?Set ORIGENLAB_RENDER_SSH_HOST (e.g. ssh.oregon.render.com)}"
: "${ORIGENLAB_RENDER_SSH_USER:?Set ORIGENLAB_RENDER_SSH_USER (srv-*)}"
: "${ORIGENLAB_RENDER_SSH_KEY:?Set ORIGENLAB_RENDER_SSH_KEY to path of SSH private key file}"

SSH_HOST="${ORIGENLAB_RENDER_SSH_HOST}"
SSH_USER="${ORIGENLAB_RENDER_SSH_USER}"
SSH_KEY="${ORIGENLAB_RENDER_SSH_KEY}"

if [[ ! -f "${SSH_KEY}" ]]; then
  echo "ERROR: SSH key file not found: ${SSH_KEY}" >&2
  exit 2
fi

# Common SSH/SCP options as a bash array — prevents key paths with whitespace
# from being word-split incorrectly.  BatchMode prevents interactive prompts;
# StrictHostKeyChecking=accept-new trusts new hosts but rejects changed keys.
SSH_OPTS=(
  -i "${SSH_KEY}"
  -o BatchMode=yes
  -o StrictHostKeyChecking=accept-new
  -o ConnectTimeout=30
)

# ---------------------------------------------------------------------------
# Preflight: validate local bundle before any network I/O
# ---------------------------------------------------------------------------
echo "== W1 institution-prospect cloud sync =="
echo "Local source: ${W1_LOCAL_DIR}"
echo "Remote host:  ${SSH_USER}@${SSH_HOST}:${W1_REMOTE_DATA}  (key: [configured])"

if [[ ! -d "${W1_LOCAL_DIR}" ]]; then
  echo "ERROR: Local W1 bundle directory not found: ${W1_LOCAL_DIR}" >&2
  echo "Run: uv run origenlab auto-refresh-chilecompra-equipment --once --apply --publish-institution-prospects" >&2
  exit 2
fi

PACKET="${W1_LOCAL_DIR}/institution_prospect_packet.json"
if [[ ! -f "${PACKET}" ]]; then
  echo "ERROR: institution_prospect_packet.json not found: ${PACKET}" >&2
  exit 2
fi

# Validate using the same Python read-model loader the API uses.  This catches
# wrong contract_version, malformed JSON/CSV, or missing queue files before we
# waste network bandwidth uploading a bundle that the remote API would reject.
echo "-- Validating local W1 bundle --"
cd "$PIPE"
uv sync --group dev >/dev/null 2>&1

LOCAL_VALIDATION_OUT="$(
  uv run python -c "
from pathlib import Path
from origenlab_email_pipeline.commercial_procurement_institution_prospects.read_model import (
    load_published_read_model,
)
result = load_published_read_model(Path('${W1_LOCAL_DIR}'))
print('contract_version:', result.get('contract_version', '?'))
print('as_of_utc:       ', result.get('as_of_utc', '?'))
print('profile_count:   ', len(result.get('profiles') or []))
cur_q = (result.get('operator_queue_sizes') or {}).get('current_opportunity_queue', '?')
print('current_opps:    ', cur_q)
print('local_validation_ok')
" 2>&1
)" || {
  echo "ERROR: Local W1 bundle failed read-model validation:" >&2
  echo "${LOCAL_VALIDATION_OUT}" >&2
  exit 2
}
echo "${LOCAL_VALIDATION_OUT}"

# ---------------------------------------------------------------------------
# Build local archive
# ---------------------------------------------------------------------------
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOCAL_ARCHIVE="/tmp/w1_institution_prospects_${TIMESTAMP}.tar.gz"
REMOTE_ARCHIVE="${W1_REMOTE_DATA}/w1_upload_${TIMESTAMP}.tar.gz"
STAGING_DIR="${W1_REMOTE_DATA}/institution_prospects.${TIMESTAMP}.staging"

echo ""
echo "-- Creating local archive: ${LOCAL_ARCHIVE} --"
tar -czf "${LOCAL_ARCHIVE}" -C "${W1_LOCAL_DIR}" .
echo "Archive size: $(du -sh "${LOCAL_ARCHIVE}" | cut -f1)"

# Always remove the local temp archive on exit.
# shellcheck disable=SC2064
trap "rm -f '${LOCAL_ARCHIVE}'; echo '-- Local archive cleaned.'" EXIT

# ---------------------------------------------------------------------------
# Upload archive to remote (staging area on the persistent disk)
# ---------------------------------------------------------------------------
echo ""
echo "-- Uploading archive to remote --"
scp "${SSH_OPTS[@]}" "${LOCAL_ARCHIVE}" "${SSH_USER}@${SSH_HOST}:${REMOTE_ARCHIVE}"

# ---------------------------------------------------------------------------
# Remote: extract → validate → atomically promote → clean old snapshots
#
# Positional args passed to bash -s:
#   $1  STAGING_DIR  versioned staging directory path
#   $2  REMOTE_ARCHIVE  uploaded tar.gz path
#   $3  W1_CANONICAL  canonical symlink/directory path
#   $4  W1_REMOTE_DATA  data root on the persistent disk
#   $5  W1_KEEP_SNAPSHOTS  number of old snapshots to retain
# ---------------------------------------------------------------------------
echo ""
echo "-- Remote: extract → validate → promote --"
ssh "${SSH_OPTS[@]}" "${SSH_USER}@${SSH_HOST}" bash -s \
  "${STAGING_DIR}" "${REMOTE_ARCHIVE}" "${W1_CANONICAL}" "${W1_REMOTE_DATA}" "${W1_KEEP_SNAPSHOTS}" \
  <<'REMOTE_SCRIPT'
set -euo pipefail

STAGING="$1"
ARCHIVE="$2"
CANONICAL="$3"
REMOTE_DATA="$4"
KEEP="${5:-2}"
NEXT_LINK="${REMOTE_DATA}/institution_prospects.next"

# Guard: canonical must not be a plain directory (could happen if someone
# manually created one before the first sync).  A plain directory would cause
# `mv` to move the symlink inside it rather than replacing it.
if [[ -d "${CANONICAL}" && ! -L "${CANONICAL}" ]]; then
  echo "ERROR: ${CANONICAL} is a real directory, not a symlink." >&2
  echo "Remove or rename it manually before running this sync." >&2
  rm -f "${ARCHIVE}"
  exit 2
fi

# Extract to the versioned staging directory.
mkdir -p "${STAGING}"
echo "Extracting to ${STAGING} …"
tar -xzf "${ARCHIVE}" -C "${STAGING}"
rm -f "${ARCHIVE}"

# Validate the staged bundle using the same Python loader the API uses.
# The remote environment has the email-pipeline package installed in the API venv.
cd /app/apps/api
if ! uv run python -c "
from pathlib import Path
from origenlab_email_pipeline.commercial_procurement_institution_prospects.read_model import (
    load_published_read_model,
)
result = load_published_read_model(Path('${STAGING}'))
print('remote_contract_version:', result.get('contract_version', '?'))
print('remote_validation_ok')
" ; then
  echo "ERROR: Remote W1 bundle validation failed. Canonical bundle preserved." >&2
  rm -rf "${STAGING}"
  exit 2
fi

# Atomic symlink promotion:
#   1. rm -f NEXT_LINK clears any stale temp symlink left by a previous failed run.
#   2. ln -s creates NEXT_LINK pointing at the new STAGING directory.
#   3. mv -T NEXT_LINK CANONICAL replaces the canonical name via rename(2) —
#      atomic on the same filesystem; a concurrent API request sees either the
#      old complete bundle or the new one, never a partial one.
#
#   -T (--no-target-directory) is REQUIRED: without it, GNU mv treats an existing
#   canonical symlink-to-directory as a *destination directory* and moves NEXT_LINK
#   *inside* the old snapshot instead of replacing the symlink.  This would leave
#   canonical pointing at the stale snapshot after the second sync, eventually
#   creating a dangling symlink when retention deletes the old snapshot.
rm -f "${NEXT_LINK}"
ln -s "${STAGING}" "${NEXT_LINK}"
mv -T "${NEXT_LINK}" "${CANONICAL}"
echo "Canonical promoted: ${CANONICAL} → ${STAGING}"

# Clean up old snapshots, keeping the ${KEEP} most recent (excluding current).
OLD_SNAPS="$(
  find "${REMOTE_DATA}" -maxdepth 1 -name 'institution_prospects.*.staging' \
    -not -path "${STAGING}" 2>/dev/null \
    | sort \
    | head -n "-${KEEP}" 2>/dev/null \
    || true
)"
if [[ -n "${OLD_SNAPS}" ]]; then
  while IFS= read -r snap; do
    [[ -d "${snap}" ]] || continue
    rm -rf "${snap}"
    echo "Removed old snapshot: ${snap}"
  done <<< "${OLD_SNAPS}"
fi
REMOTE_SCRIPT

echo ""
echo "============================================================"
echo "W1 institution-prospect cloud sync complete."
echo "  Snapshot: ${STAGING_DIR}"
echo "  Canonical: ${W1_CANONICAL} → ${STAGING_DIR}"
echo ""
echo "Verify: GET /operator/procurement/status"
echo "  expect: meta.reduced_mode=false"
echo "  expect: meta.canonical_reason=institution_prospect_read_model"
echo "============================================================"
