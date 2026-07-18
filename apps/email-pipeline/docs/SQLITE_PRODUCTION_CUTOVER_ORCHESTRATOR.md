# SQLite production cutover orchestrator

**Status:** hardened staged orchestrator + synthetic tests (draft).  
**Does not authorize** running a real production cutover from this PR alone.

Implements the fail-closed tool required by [`SQLITE_WRITABLE_RESTORE_AND_CUTOVER.md`](SQLITE_WRITABLE_RESTORE_AND_CUTOVER.md).

## Corrected state invariants

1. Git identity comes from bounded `git rev-parse` only (never env SHA). Production apply requires branch `main`, clean worktree, and `main == origin/main`. Exact 40-character SHA match.
2. Real production path must be `canonical_production_sqlite_path()` (samefile/device/inode). Synthetic paths only with `allow_synthetic_world=True` + `SyntheticWorld`.
3. First mutating stage seals an **immutable approved plan** (public journal basenames + private path journal). Later CLI options cannot drift.
4. Every mutating stage holds an exclusive `flock` keyed by production device/inode + maintenance ID. Zero-write preflight creates no lock.
5. `writer_resume_started=true` is fsynced **before** removing the first pause marker; automatic rollback is permanently refused afterward.
6. Atomic swap uses `renameat2(RENAME_EXCHANGE)` then `renameat2(RENAME_NOREPLACE)` for pre-cutover retention. Crash reconciliation never blindly repeats exchange.
7. Read-only smoke starts **API only**; health timer starts in `resume_services`.

## State diagram

```text
plan_preflight (zero-write, no lock)
        |
        v
pause_writers  --seal approved plan; touch pause markers
        |
        v
stop_readers   --stop health timer, then API
        |
        v
quiesce_wal    --wal_checkpoint(TRUNCATE); busy==0; recheck barriers
        |
        v
create_current_backup   --Online Backup API → approved /mnt/d dest
        |
        v
compact_to_production_fs_staging  --VACUUM INTO staging on production FS
        |
        v
verify_candidate
        |
        v
approve_swap   --separate --approve-swap
        |
        v
atomic_swap    --swap_intent → RENAME_EXCHANGE → RENAME_NOREPLACE retain
        |
        v
readonly_smoke --API only; semantic checks; sidecar/SHM policy
        |
        v
resume_services --start health timer after smoke
        |
        v
resume_writers_mail           --fsync writer_resume_started; remove mail pause
resume_writers_observe_mail
resume_writers_mirror
resume_writers_observe_mirror
resume_writers_commit         --writers_resumed=true
        |
        v
completed
```

Each arrow is a **separate** CLI invocation with `--apply` (except `plan_preflight`).

## Writer entry points

| Entry | Barrier | Status |
|-------|---------|--------|
| `mail_auto_refresh` | `auto_refresh_paused` | guarded |
| `dashboard_auto_mirror` | `dashboard_auto_mirror_paused` | guarded |
| `chilecompra_equipment_auto_refresh` | lock only | **unguarded** |
| `origenlab-api.service` | stop_readers | guarded via stop |
| ad-hoc operator scripts | none | **unguarded** |

**`REAL_PRODUCTION_APPLY_BLOCKED = True`** until every unguarded entry gains a reliable maintenance barrier. SyntheticWorld tests still exercise the full machine.

## Implemented vs stubbed (real adapters)

| Capability | Status |
|------------|--------|
| Online Backup via `run_online_backup` | **Implemented** in `FilesystemAdapters` |
| Offline compact via `run_offline_compaction` | **Implemented** in `FilesystemAdapters` |
| WAL checkpoint busy/log validation | **Implemented** |
| Exclusive flock | **Implemented** |
| Canonical path + git identity | **Implemented** |
| Real mutating apply against production | **Blocked** (`REAL_PRODUCTION_APPLY_BLOCKED`) |

## Crash-reconciliation matrix

| Condition | Recognized | Safe action |
|-----------|------------|-------------|
| Intent written; prod=old, staging=new; no pre_cutover | `pre_exchange_ready` | retry atomic swap from intent |
| `exchange_completed`; staging still holds old | `exchange_done_retain_pending` | `RENAME_NOREPLACE` retain only |
| prod=new, pre_cutover=old, staging absent | `swap_complete` | continue readonly_smoke |
| Fingerprints disagree / unexpected layout | `ambiguous_*` | manual inspect; **never** repeat exchange |
| Crash after `writer_resume_started` | n/a | rollback refused |
| Crash before journal write | n/a | re-run same stage after inspect |

## Rollback rules

- Allowed only before `writer_resume_started`.
- Pre-cutover path must match journal basename/path exactly (arbitrary `--pre-cutover-path` refused).
- Require writers paused, no live locks/FDs, API+health stopped, same FS, exchange capability.
- After rollback: `readonly_smoke` required before services resume.

## SHM / sidecar smoke policy

- `-journal`: forbidden
- `-wal`: fail if size > 0
- `-shm`: allowed only if size ∈ `{0, 32768}` after API open

## Remaining blockers before a real cutover

1. Add cutover-linked pause barriers for chilecompra + document/handle ad-hoc writers; clear `REAL_PRODUCTION_APPLY_BLOCKED`.
2. Operator maintenance window + capacity confirmation.
3. Prove `renameat2(RENAME_EXCHANGE)` / `RENAME_NOREPLACE` on the production filesystem.
4. Explicit human approval of maintenance ID + fingerprints at swap time.
5. End-to-end dry run still must not mutate production until blockers clear.

## Module / tests

- `origenlab_email_pipeline.qa.sqlite_production_cutover`
- `scripts/maintenance/orchestrate_sqlite_production_cutover.py`
- `tests/test_sqlite_production_cutover.py`
