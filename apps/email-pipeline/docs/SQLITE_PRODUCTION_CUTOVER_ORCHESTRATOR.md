# SQLite production cutover orchestrator

**Status:** hardened staged orchestrator + synthetic tests (draft).  
**Does not authorize** running a real production cutover from this PR alone.

Implements the fail-closed tool required by [`SQLITE_WRITABLE_RESTORE_AND_CUTOVER.md`](SQLITE_WRITABLE_RESTORE_AND_CUTOVER.md).

## Corrected access classification

| Class | Entry | Barrier |
|-------|-------|---------|
| **SQLite writers** | `mail_auto_refresh` / daily-core | `auto_refresh_paused` (must pause) |
| **SQLite writers** | ad-hoc operator scripts | OS chmod write barrier (mitigates accidental RW opens) |
| **SQLite readers** | `dashboard_auto_mirror` | `dashboard_auto_mirror_paused` (release SQLite reads; stop stale Postgres publish) |
| **SQLite readers** | `origenlab-api` + health timer | `systemctl stop` |
| **Unrelated external** | `chilecompra_equipment_auto_refresh` | optional quiet only — **does not mutate SQLite**; **must not block RPO=0** |

Threat model: the OS write barrier blocks ordinary accidental ad-hoc SQLite writes. Malicious/root processes can bypass chmod and are out of scope.

## Corrected state invariants

1. Git identity from bounded `git rev-parse` only (never env SHA).
2. Canonical production samefile/device/inode (synthetic only with `allow_synthetic_world` + `SyntheticWorld`).
3. Immutable approved plan + private path journal (incl. original mode/uid/gid).
4. Exclusive flock keyed **only** by production device+inode (maintenance ID is diagnostic payload only).
5. After quiesce: record mode/owner → `chmod(mode & ~0222)` → fsync → FD rescan → keep RO through backup/compact/verify/swap/smoke.
6. `writer_resume_started` fsynced **before** restoring writable mode; automatic rollback forbidden from that point.
7. Post-mail observation accepts size/mtime fingerprint drift; refuses device/inode replacement.

## State diagram

```text
plan_preflight (zero-write, no lock)
        |
        v
pause_writers → stop_readers → quiesce_wal
        |
        v
apply_os_write_barrier   --chmod remove write bits; journal original mode
        |
        v
create_current_backup → compact → verify → approve_swap (staging RO)
        |
        v
atomic_swap → readonly_smoke (API only) → resume_services
        |
        v
resume_writers_ponr → resume_writers_restore_mode
        → resume_writers_mail → observe_mail
        → resume_writers_mirror → observe_mirror
        → resume_writers_commit → completed

abort_before_swap (operation): only before swap intent/exchange and before PoNR
```

## Write-barrier state transitions

| Transition | Journal |
|------------|---------|
| Intent before chmod | `permission_intent` |
| Barrier active | `production_write_barrier_active=true`, `original_mode/uid/gid` |
| Staging RO before swap | `staging_write_barrier_active=true` |
| PoNR | `writer_resume_started=true` (rollback refused) |
| Restore writable | `writable_mode_restored=true`, barrier cleared |
| Abort before swap | restore mode → API smoke → health → remove pauses |

## Abort / recovery matrix

| Condition | Action |
|-----------|--------|
| Before swap intent | `abort_before_swap` restores perms/services/markers |
| After swap intent/exchange | abort refused |
| After `writer_resume_started` | abort + automatic rollback refused |
| Crash mid-chmod | inspect mode + journal; resume documented next stage |
| `pre_exchange_ready` / retain pending / swap complete | same reconciliation as before |

## Production apply readiness (derived)

Not a hard-coded `True`. Later stages require: SQLite automation writers paused, readers stopped, exclusive flock held, OS write barrier active, FD scan clean, WAL quiesced, approved plan valid. ChileCompra reclassification alone does not clear readiness.

## Remaining environment-only blockers

1. Operator maintenance window + capacity on `/mnt/d` backup and `/` staging.
2. Prove `renameat2(RENAME_EXCHANGE|NOREPLACE)` on the production filesystem.
3. Explicit human approval of maintenance ID + fingerprints at swap.
4. Live dry-run still must not mutate production until an approved window.

## Module / tests

- `origenlab_email_pipeline.qa.sqlite_production_cutover`
- `scripts/maintenance/orchestrate_sqlite_production_cutover.py`
- `tests/test_sqlite_production_cutover.py`
