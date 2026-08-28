#!/usr/bin/env bash
# T1 ANEXO tender-terms publication → Render persistent disk (staged, atomic).
#
# Transfers an already-published local T1 canonical publication
# (summary.json + tender_terms.jsonl) to the Render API's persistent disk
# (/var/data) using scp + ssh. Mirrors sync_institution_prospects_to_cloud.sh
# exactly in structure. The API never reads a partially uploaded publication:
# the remote script extracts to a versioned staging directory and only
# promotes after a SHA-256 integrity check and required-file sanity check
# pass; only a verified snapshot atomically replaces the canonical symlink.
#
# ATOMICITY MODEL:
#   1. Local source publication validated by Python (load_published_tender_terms).
#   2. tar.gz archive created locally.
#   3. Archive extracted locally to a temp directory; extracted snapshot
#      validated by Python — proves the exact packaged bytes are valid and
#      eliminates a source-directory race between step 1 and step 2.
#   4. SHA-256 of archive computed locally (sha256sum).
#   5. Archive uploaded via scp.
#   6. Remote script:
#      a. Verifies SHA-256 of received archive (sha256sum) — fail closed if mismatch.
#      b. Extracts to versioned staging dir: /var/data/tender_terms.TIMESTAMP.staging/
#      c. Checks all required T1 files exist (defense-in-depth; SHA is the primary proof).
#      d. Atomically promotes:
#           rm -f NEXT_LINK
#           ln -s STAGING NEXT_LINK
#           mv -T NEXT_LINK CANONICAL   ← rename(2), no Python, no uv
#   A concurrent API request sees either the old or new complete publication, never a partial one.
#   The API's resolved_tender_terms_dir() follows the symlink on every request,
#   so no restart is required after promotion.
#
# FAILURE BEHAVIOUR:
#   - Missing / invalid local publication → exit 2 before any network I/O.
#   - Invalid locally extracted archive → exit 2 before SCP.
#   - Failed SCP upload → canonical publication untouched, exit non-zero.
#   - Remote SHA-256 mismatch → staging not created, canonical untouched, exit 2.
#   - Missing required file after extraction → staging cleaned, canonical untouched, exit 2.
#   - Failed symlink promotion → canonical untouched, staging dir left for inspection, exit 2.
#
# RUNTIME AGNOSTIC:
#   The remote script uses only POSIX tools (bash, tar, sha256sum, find, ln, mv).
#   No Python, uv, pip, Ollama, nor any Render/Docker-specific filesystem layout
#   is assumed.
#
# SAFETY:
#   - No SQLite/Postgres writes, Gmail, outreach, or T1/W1 regeneration.
#   - No ChileCompra acquisition (this script transfers an already-published
#     local T1 publication produced by `publish_tender_terms`; it never
#     acquires attachments itself).
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
#   ORIGENLAB_TENDER_TERMS_LOCAL_DIR     Local T1 source directory
#                                         (default: reports/out/active/current/tender_terms)
#   ORIGENLAB_TENDER_TERMS_REMOTE_DATA   Remote persistent data root on disk
#                                         (default: /var/data)
#   ORIGENLAB_TENDER_TERMS_KEEP_SNAPSHOTS  Old remote snapshots to retain after promotion
#                                           (default: 2; must be >= 1)
#
# See sync_institution_prospects_to_cloud.sh for the W1 counterpart this
# mirrors, and apps/email-pipeline/docs/PHASE1_CLOUD_READ_PATH.md §4.
set -euo pipefail

PIPE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# shellcheck source=scripts/ops/_load_pipeline_dotenv.sh
source "${PIPE}/scripts/ops/_load_pipeline_dotenv.sh"
load_pipeline_dotenv "$PIPE"

# ---------------------------------------------------------------------------
# Configuration (env-overridable, never secret)
# ---------------------------------------------------------------------------
TT_LOCAL_DIR="${ORIGENLAB_TENDER_TERMS_LOCAL_DIR:-${PIPE}/reports/out/active/current/tender_terms}"
TT_REMOTE_DATA="${ORIGENLAB_TENDER_TERMS_REMOTE_DATA:-/var/data}"
TT_KEEP_SNAPSHOTS="${ORIGENLAB_TENDER_TERMS_KEEP_SNAPSHOTS:-2}"
TT_CANONICAL="${TT_REMOTE_DATA}/tender_terms"

# Validate TT_KEEP_SNAPSHOTS before any network I/O.
if ! [[ "${TT_KEEP_SNAPSHOTS}" =~ ^[0-9]+$ ]] || [[ "${TT_KEEP_SNAPSHOTS}" -lt 1 ]]; then
  echo "ERROR: ORIGENLAB_TENDER_TERMS_KEEP_SNAPSHOTS must be a decimal integer >= 1 (got: '${TT_KEEP_SNAPSHOTS}')." >&2
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
# Preflight: validate local source publication before any I/O
# ---------------------------------------------------------------------------
echo "== T1 tender-terms cloud sync =="
echo "Local source: ${TT_LOCAL_DIR}"
echo "Remote host:  ${SSH_USER}@${SSH_HOST}:${TT_REMOTE_DATA}  (key: [configured])"

if [[ ! -d "${TT_LOCAL_DIR}" ]]; then
  echo "ERROR: Local T1 publication directory not found: ${TT_LOCAL_DIR}" >&2
  echo "This script never acquires or publishes T1 itself -- it only ships" >&2
  echo "whatever is already published locally. Produce a local publication" >&2
  echo "first, either via:" >&2
  echo "  (a) auto-refresh-chilecompra-equipment --once --apply \\" >&2
  echo "        --publish-institution-prospects --publish-tender-terms" >&2
  echo "      (the opt-in production path: acquires + publishes T1 in-process" >&2
  echo "      for this run's current W1 opportunity queue), or" >&2
  echo "  (b) the manual replay path, for republishing from already-cached" >&2
  echo "      bundles without re-acquiring:" >&2
  echo "        uv run python scripts/commercial/publish_tender_terms_from_cached_bundles.py \\" >&2
  echo "          --bundles-dir <path to cached TenderAttachmentBundle *.pkl files> \\" >&2
  echo "          --out-dir ${TT_LOCAL_DIR}" >&2
  exit 2
fi

SUMMARY_JSON="${TT_LOCAL_DIR}/summary.json"
if [[ ! -f "${SUMMARY_JSON}" ]]; then
  echo "ERROR: summary.json not found: ${SUMMARY_JSON}" >&2
  exit 2
fi

# Validate using the same Python publication loader the API uses.  This catches
# wrong contract_version, malformed JSON/JSONL, or a reconciliation mismatch
# before we waste network bandwidth uploading a publication the remote API
# would reject.
echo "-- Validating local T1 source publication --"
cd "$PIPE"
uv sync --group dev >/dev/null 2>&1

LOCAL_VALIDATION_OUT="$(
  uv run python -c "
from pathlib import Path
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms.production_publish import (
    load_published_tender_terms,
)
result = load_published_tender_terms(Path('${TT_LOCAL_DIR}'))
summary = result.get('summary') or {}
print('contract_version:', summary.get('contract_version', '?'))
print('terms_version:   ', summary.get('terms_version', '?'))
print('as_of_utc:       ', summary.get('as_of_utc', '?'))
print('tender_count:    ', len(result.get('tender_terms') or []))
print('local_source_validation_ok')
" 2>&1
)" || {
  echo "ERROR: Local T1 source publication failed validation:" >&2
  echo "${LOCAL_VALIDATION_OUT}" >&2
  exit 2
}
echo "${LOCAL_VALIDATION_OUT}"

# ---------------------------------------------------------------------------
# Build local archive, extract it locally, validate the extraction, compute SHA
# ---------------------------------------------------------------------------
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOCAL_ARCHIVE="/tmp/t1_tender_terms_${TIMESTAMP}.tar.gz"
REMOTE_ARCHIVE="${TT_REMOTE_DATA}/t1_upload_${TIMESTAMP}.tar.gz"
STAGING_DIR="${TT_REMOTE_DATA}/tender_terms.${TIMESTAMP}.staging"

echo ""
echo "-- Creating local archive: ${LOCAL_ARCHIVE} --"
tar -czf "${LOCAL_ARCHIVE}" -C "${TT_LOCAL_DIR}" .
echo "Archive size: $(du -sh "${LOCAL_ARCHIVE}" | cut -f1)"

# Create the local extraction directory via mktemp to guarantee a unique,
# controlled path (safe to rm -rf on cleanup).
LOCAL_EXTRACT_DIR="$(mktemp -d /tmp/t1_extract_XXXXXXXX)"

# Clean up both the archive and the local extraction directory on every exit.
# shellcheck disable=SC2064
trap "rm -f '${LOCAL_ARCHIVE}'; rm -rf '${LOCAL_EXTRACT_DIR}'; echo '-- Local archive and extract dir cleaned.'" EXIT

echo ""
echo "-- Validating local extracted archive (proves packaged bytes match source) --"
tar -xzf "${LOCAL_ARCHIVE}" -C "${LOCAL_EXTRACT_DIR}"

LOCAL_EXTRACT_VALIDATION_OUT="$(
  uv run python -c "
from pathlib import Path
from origenlab_email_pipeline.commercial_procurement_anexo_tender_terms.production_publish import (
    load_published_tender_terms,
)
result = load_published_tender_terms(Path('${LOCAL_EXTRACT_DIR}'))
summary = result.get('summary') or {}
print('extracted_contract_version:', summary.get('contract_version', '?'))
print('extracted_tender_count:    ', len(result.get('tender_terms') or []))
print('local_extracted_validation_ok')
" 2>&1
)" || {
  echo "ERROR: Local extracted archive failed validation (archive may be corrupt):" >&2
  echo "${LOCAL_EXTRACT_VALIDATION_OUT}" >&2
  exit 2
}
echo "${LOCAL_EXTRACT_VALIDATION_OUT}"

# Compute SHA-256 of the validated archive.  sha256sum is standard on Linux
# (GNU coreutils); the remote Render host is Linux so only sha256sum is needed
# there.  shasum -a 256 is a fallback for macOS local runs.
if command -v sha256sum >/dev/null 2>&1; then
  ARCHIVE_SHA256="$(sha256sum "${LOCAL_ARCHIVE}" | awk '{print $1}')"
elif command -v shasum >/dev/null 2>&1; then
  ARCHIVE_SHA256="$(shasum -a 256 "${LOCAL_ARCHIVE}" | awk '{print $1}')"
else
  echo "ERROR: Neither sha256sum nor shasum found. Cannot compute archive integrity hash." >&2
  exit 2
fi
echo "Archive SHA-256: ${ARCHIVE_SHA256}"

# ---------------------------------------------------------------------------
# Upload archive to remote (staging area on the persistent disk)
# ---------------------------------------------------------------------------
echo ""
echo "-- Uploading archive to remote --"
scp "${SSH_OPTS[@]}" "${LOCAL_ARCHIVE}" "${SSH_USER}@${SSH_HOST}:${REMOTE_ARCHIVE}"

# ---------------------------------------------------------------------------
# Remote: verify SHA → extract → sanity-check files → atomically promote → retain
#
# Positional args passed to bash -s:
#   $1  STAGING_DIR      versioned staging directory path
#   $2  REMOTE_ARCHIVE   uploaded tar.gz path
#   $3  TT_CANONICAL     canonical symlink/directory path
#   $4  TT_REMOTE_DATA   data root on the persistent disk
#   $5  TT_KEEP_SNAPSHOTS  number of old snapshots to retain
#   $6  EXPECTED_SHA256  SHA-256 hex of the locally validated archive
#
# The remote script is intentionally runtime-agnostic: it uses only POSIX
# tools (bash, tar, sha256sum, find, ln, mv).  No Python, uv, Ollama, or
# specific filesystem layout is assumed.
# ---------------------------------------------------------------------------
echo ""
echo "-- Remote: verify SHA → extract → promote --"
ssh "${SSH_OPTS[@]}" "${SSH_USER}@${SSH_HOST}" bash -s \
  "${STAGING_DIR}" "${REMOTE_ARCHIVE}" "${TT_CANONICAL}" "${TT_REMOTE_DATA}" "${TT_KEEP_SNAPSHOTS}" "${ARCHIVE_SHA256}" \
  <<'REMOTE_SCRIPT'
set -euo pipefail

STAGING="$1"
ARCHIVE="$2"
CANONICAL="$3"
REMOTE_DATA="$4"
KEEP="${5:-2}"
EXPECTED_SHA="${6:?SHA-256 of archive expected as \$6}"
NEXT_LINK="${REMOTE_DATA}/tender_terms.next"

# ---------------------------------------------------------------------------
# Step 1: Verify SHA-256 of the received archive (primary integrity proof).
#   Fail closed and preserve canonical if there is any mismatch.
# ---------------------------------------------------------------------------
ACTUAL_SHA="$(sha256sum "${ARCHIVE}" | awk '{print $1}')"
if [[ "${ACTUAL_SHA}" != "${EXPECTED_SHA}" ]]; then
  echo "ERROR: Archive SHA-256 mismatch." >&2
  echo "  expected: ${EXPECTED_SHA}" >&2
  echo "  actual:   ${ACTUAL_SHA}" >&2
  rm -f "${ARCHIVE}"
  exit 2
fi
echo "SHA-256 verified: ${ACTUAL_SHA}"

# ---------------------------------------------------------------------------
# Step 2: Guard — canonical must not be a plain directory (could happen if
#   someone manually created one before the first sync).  A plain directory
#   would cause mv to move NEXT_LINK inside it rather than replacing it.
# ---------------------------------------------------------------------------
if [[ -d "${CANONICAL}" && ! -L "${CANONICAL}" ]]; then
  echo "ERROR: ${CANONICAL} is a real directory, not a symlink." >&2
  echo "Remove or rename it manually before running this sync." >&2
  rm -f "${ARCHIVE}"
  exit 2
fi

# ---------------------------------------------------------------------------
# Step 3: Extract to the versioned staging directory.
# ---------------------------------------------------------------------------
mkdir -p "${STAGING}"
echo "Extracting to ${STAGING} …"
tar -xzf "${ARCHIVE}" -C "${STAGING}"
rm -f "${ARCHIVE}"

# ---------------------------------------------------------------------------
# Step 4: Require minimum valid T1 publication files (defense-in-depth).
#   SHA-256 is the primary integrity proof; this guard catches truncated or
#   mis-packaged archives where the hash still matches the truncated file.
#   Files must match what load_published_tender_terms() requires:
#     summary.json, tender_terms.jsonl
# ---------------------------------------------------------------------------
_REQUIRED_FILES=(
  "summary.json"
  "tender_terms.jsonl"
)
for _f in "${_REQUIRED_FILES[@]}"; do
  if [[ ! -f "${STAGING}/${_f}" ]]; then
    echo "ERROR: Required T1 file missing from extracted publication: ${_f}" >&2
    rm -rf "${STAGING}"
    exit 2
  fi
done
echo "Required T1 files verified (${#_REQUIRED_FILES[@]} files present)."

# ---------------------------------------------------------------------------
# Step 5: Atomic symlink promotion.
#   1. rm -f NEXT_LINK clears any stale temp symlink left by a previous failed run.
#   2. ln -s creates NEXT_LINK pointing at the new STAGING directory.
#   3. mv -T NEXT_LINK CANONICAL replaces the canonical name via rename(2) —
#      atomic on the same filesystem; a concurrent API request sees either the
#      old complete publication or the new one, never a partial one.
#
#   -T (--no-target-directory) is REQUIRED: without it, GNU mv treats an existing
#   canonical symlink-to-directory as a *destination directory* and moves NEXT_LINK
#   *inside* the old snapshot instead of replacing the symlink.  This would leave
#   canonical pointing at the stale snapshot after the second sync, eventually
#   creating a dangling symlink when retention deletes the old snapshot.
# ---------------------------------------------------------------------------
rm -f "${NEXT_LINK}"
ln -s "${STAGING}" "${NEXT_LINK}"
mv -T "${NEXT_LINK}" "${CANONICAL}"
echo "Canonical promoted: ${CANONICAL} → ${STAGING}"

# ---------------------------------------------------------------------------
# Step 6: Clean up old snapshots, keeping the ${KEEP} most recent (excluding current).
# ---------------------------------------------------------------------------
OLD_SNAPS="$(
  find "${REMOTE_DATA}" -maxdepth 1 -name 'tender_terms.*.staging' \
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
echo "T1 tender-terms cloud sync complete."
echo "  Snapshot: ${STAGING_DIR}"
echo "  Canonical: ${TT_CANONICAL} → ${STAGING_DIR}"
echo ""
echo "Verify: GET /operator/procurement/tenders/{tender_code}"
echo "  expect: t1_meta.reduced_mode=false"
echo "  expect: t1_meta.canonical_reason=tender_terms_read_model"
echo "============================================================"
