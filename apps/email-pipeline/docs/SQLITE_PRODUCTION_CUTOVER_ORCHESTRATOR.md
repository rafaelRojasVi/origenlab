# SQLite production cutover orchestrator

**Status:** hardened staged orchestrator + synthetic tests (draft).  
**Does not authorize** running a real production cutover from this PR alone.

Three distinct modes (do not conflate):

| Mode | What it does |
|------|----------------|
| Writable **RO rehearsal** | Separate tool (`sqlite_writable_restore_rehearsal`) — scratch fixtures only |
| **Zero-write production preflight** | `plan_preflight` — reports only; no locks, journals, pauses, chmod |
| **Real staged apply** | Separate `--apply` per stage with high-friction auth |

The July 2026 compact candidate is **not eligible** for production cutover (evidence only).

## Corrected access classification

| Class | Entry | Barrier |
|-------|-------|---------|
| **SQLite writers** | `mail_auto_refresh` / daily-core | `auto_refresh_paused` |
| **SQLite writers** | ad-hoc operator scripts | OS chmod write barrier (accidental RW opens) |
| **SQLite readers** | `dashboard_auto_mirror` | `dashboard_auto_mirror_paused` |
| **SQLite readers** | `origenlab-api` + health timer | `systemctl stop` |
| **Unrelated external** | `chilecompra_equipment_auto_refresh` | optional quiet — **does not open/mutate SQLite**; **must not block RPO=0** |

Threat model: chmod write barrier blocks ordinary accidental ad-hoc SQLite writes. **Malicious/root bypass is explicitly out of scope.**

## State diagram

```text
plan_preflight (zero-write, no lock)
        |
        v
pause_writers → stop_readers → quiesce_wal
        |
        v
apply_os_write_barrier
  -- journal original_mode + intent
  -- open FD, fstat match device+inode, fchmod(~write)
  -- fsync; verify; rescan FDs
        |
        v
create_current_backup → compact → verify
        |
        v
approve_swap (staging owner + RO) → atomic_swap → readonly_smoke → resume_services
        |
        v
resume_writers_ponr          -- writer_resume_started (rollback forbidden)
resume_writers_restore_mode  -- fchmod restore writable on verified inode
resume_writers_mail          -- remove mail pause
resume_writers_observe_mail  -- accept size/mtime drift; on failure RE-PAUSE mail
resume_writers_mirror        -- only if mail_observe_ok
resume_writers_observe_mirror
resume_writers_commit → completed

abort_before_swap: before swap_intent AND before PoNR only
```

## Write-barrier / abort recovery matrix

| Condition | Safe action |
|-----------|-------------|
| Intent written; chmod not yet applied | Re-run `apply_os_write_barrier` or `abort_before_swap` |
| Chmod applied; `barrier_active` not yet journaled | `abort_before_swap` restores from `original_mode` / `permission_intent` / private plan |
| `barrier_active=true`, pre-swap | Continue stages or `abort_before_swap` |
| After `swap_intent` / exchange | Abort refused; use swap reconciliation / rollback rules |
| After `writer_resume_started` | Abort + automatic rollback refused |
| Mail observe failed | Mail re-paused; mirror blocked; fix then re-run observe |

`reconcile_permission_barrier` inspects mode vs journal and reports the recognized state — it never silently leaves writers unpaused with an unknown DB mode.

## Final human approval boundary

Real apply requires **all** of:

1. `--confirm-production-cutover`
2. Unique `--maintenance-id` matching `^[A-Za-z0-9][A-Za-z0-9._-]{7,63}$`
3. Exact 40-char `--expected-main-sha` matching `git rev-parse HEAD` (and `main` / clean / `origin/main` for non-synthetic)
4. Exact `--expected-production-path` + fingerprint matching canonical production
5. Separate `--approve-swap` for `approve_swap` / `atomic_swap`
6. Operator maintenance window + capacity confirmation on target topology

No interactive fingerprint echo is implemented; the operator must supply the exact tokens.

## Remaining environment-only blockers

1. Live maintenance window + disk capacity on `/mnt/d` and `/`.
2. Prove `renameat2(RENAME_EXCHANGE|NOREPLACE)` on the production filesystem.
3. Explicit human approval of the tokens above at swap time.
4. Keep **draft** until an approved window; this PR does not authorize a real cutover.

## Module / tests

- `origenlab_email_pipeline.qa.sqlite_production_cutover`
- `scripts/maintenance/orchestrate_sqlite_production_cutover.py`
- `tests/test_sqlite_production_cutover.py`
