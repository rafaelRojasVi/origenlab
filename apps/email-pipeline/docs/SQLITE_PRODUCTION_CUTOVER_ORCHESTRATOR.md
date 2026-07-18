# SQLite production cutover orchestrator

**Status:** staged orchestrator + synthetic tests.  
**Does not authorize** running a real production cutover from this PR alone.

Implements the fail-closed tool required by [`SQLITE_WRITABLE_RESTORE_AND_CUTOVER.md`](SQLITE_WRITABLE_RESTORE_AND_CUTOVER.md).

## State diagram

```text
plan_preflight (zero-write)
        |
        v
pause_writers  --touch auto_refresh_paused + dashboard_auto_mirror_paused
        |
        v
stop_readers   --stop origenlab-api-health.timer + origenlab-api.service
        |
        v
quiesce_wal    --checkpoint TRUNCATE; fail if writers/WAL inconsistent
        |
        v
create_current_backup   --Online Backup API → /mnt/d (fresh only)
        |
        v
compact_to_production_fs_staging  --VACUUM INTO staging on / (same FS)
        |
        v
verify_candidate
        |
        v
approve_swap   --separate --approve-swap
        |
        v
atomic_swap    --renameat2(RENAME_EXCHANGE) only; retain pre_cutover.*
        |
        v
readonly_smoke --/health /operator/status /operator/automation-status
        |
        v
resume_services
        |
        v
resume_writers --remove pause markers
        |
        v
completed
```

Each arrow is a **separate** CLI invocation with `--apply` (except `plan_preflight`).

## CLI

```bash
cd apps/email-pipeline

# Zero-write plan
uv run --frozen python scripts/maintenance/orchestrate_sqlite_production_cutover.py \
  --stage plan_preflight \
  --json

# Example mutating stage (still requires full auth flags)
uv run --frozen python scripts/maintenance/orchestrate_sqlite_production_cutover.py \
  --stage pause_writers \
  --apply \
  --confirm-production-cutover \
  --maintenance-id cutoverYYYYMMDDTHHMMSSZ \
  --expected-main-sha <sha> \
  --expected-production-path /exact/path/emails.sqlite \
  --expected-production-fingerprint '<size:mtime_ns:dev:ino>' \
  --reports-dir <reports/out> \
  --json
```

Swap stages additionally require `--approve-swap`.

## Authorization

| Flag | Required when |
|------|----------------|
| `--confirm-production-cutover` | any `--apply` stage |
| `--maintenance-id` | unique id, no path separators |
| `--expected-main-sha` | apply |
| `--expected-production-path` | exact path + basename |
| `--expected-production-fingerprint` | `size:mtime_ns:device:inode` |
| `--approve-swap` | `approve_swap` and `atomic_swap` |

Rejects: July/offline compact evidence basenames, symlinks, path/fingerprint drift, non-sequential stages, ambiguous journals.

## Journal

Durable JSON journal (atomic write + fsync) under:

`{production_parent}/.origenlab_cutover_journals/{maintenance_id}.journal.json`

Records stage, fingerprints, service/writer flags, backup/staging basenames, swap direction, `writers_resumed`, and safe next actions. On resume, reality + journal must agree; ambiguous states refuse.

## Recovery matrix

| Condition | Action |
|-----------|--------|
| Crash after stage journal write | Re-run **next** stage only |
| Crash before journal write | Re-run **same** stage after inspecting filesystem |
| `renameat2` unsupported | **Fail closed** — no dual-`mv` fallback |
| Rollback before `writers_resumed` | `--rollback-before-writers` with exact old/new fingerprints |
| Rollback after writers resumed | **Refused** — incident/reconciliation only |
| Evidence compact as source | **Refused** |

## RPO=0 invariants

Writers remain paused from `create_current_backup` through `readonly_smoke`. API + health timer stopped before `quiesce_wal` / swap. Fresh Online Backup on `/mnt/d`; compact staging on `/` beside production. Never hot `cp`, in-place VACUUM, or production `VACUUM INTO`.

## Remaining blockers before a real cutover

1. Wire real Online Backup / offline compact adapters into the stage runners (today they fail closed with operator recovery hints in the default `FilesystemAdapters`).
2. Operator maintenance window with capacity confirmation.
3. Successful synthetic drill on this tooling in the target environment’s rename-exchange capability.
4. Explicit human approval of maintenance ID + fingerprints at swap time.

## Module / tests

- `origenlab_email_pipeline.qa.sqlite_production_cutover`
- `scripts/maintenance/orchestrate_sqlite_production_cutover.py`
- `tests/test_sqlite_production_cutover.py`
